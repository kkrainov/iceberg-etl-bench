// Licensed under the Apache License, Version 2.0 (see LICENSE in the repository root).

//! DataFusion catalog `iceberg` backed by an Iceberg REST catalog.
//!
//! Schemas are Iceberg namespaces and tables resolve lazily through the catalog.
//! Each table is a datafusion-distributed-iceberg `IcebergCatalogTableProvider`
//! (the provider `dfd` uses too), which reloads table metadata on every scan, so
//! a query always reads the current snapshot. Providers are cached per table only
//! for their schema.

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use async_trait::async_trait;
use datafusion::catalog::{CatalogProvider, SchemaProvider, TableProvider};
use datafusion::error::{DataFusionError, Result};
use datafusion_distributed_iceberg::IcebergCatalogTableProvider;
use iceberg::{Catalog, CatalogBuilder, NamespaceIdent};
use iceberg_catalog_rest::RestCatalogBuilder;
use iceberg_storage_opendal::OpenDalResolvingStorageFactory;
use log::{info, warn};

fn ice(e: iceberg::Error) -> DataFusionError {
    DataFusionError::Context(
        format!("IcebergError({})", e.kind()),
        Box::new(DataFusionError::Execution(e.to_string())),
    )
}

fn is_not_found(e: &DataFusionError) -> bool {
    matches!(e, DataFusionError::Context(c, _)
        if c == "IcebergError(TableNotFound)" || c == "IcebergError(NamespaceNotFound)")
}

/// Connects to the REST catalog. `props` holds `uri`, `warehouse` and the `s3.*`
/// FileIO properties (endpoint, region, path style, static credentials).
pub async fn connect_rest(name: &str, props: HashMap<String, String>) -> Result<Arc<dyn Catalog>> {
    let catalog = RestCatalogBuilder::default()
        .with_storage_factory(Arc::new(OpenDalResolvingStorageFactory::new()))
        .with_runtime(iceberg::Runtime::current())
        .load(name, props)
        .await
        .map_err(ice)?;
    Ok(Arc::new(catalog))
}

#[derive(Debug)]
pub struct IcebergCatalogProvider {
    catalog: Arc<dyn Catalog>,
    schemas: RwLock<BTreeMap<String, Arc<IcebergSchema>>>,
}

impl IcebergCatalogProvider {
    /// Lists the namespaces, retrying while the catalog is starting.
    pub async fn new(catalog: Arc<dyn Catalog>, wait: Duration) -> Result<Arc<Self>> {
        let provider = Arc::new(Self {
            catalog,
            schemas: RwLock::new(BTreeMap::new()),
        });
        let deadline = std::time::Instant::now() + wait;
        loop {
            match provider.refresh().await {
                Ok(names) => {
                    info!("Iceberg namespaces: {}", names.join(", "));
                    return Ok(provider);
                }
                Err(e) if std::time::Instant::now() < deadline => {
                    warn!("catalog not ready ({e}); retrying");
                    tokio::time::sleep(Duration::from_secs(2)).await;
                }
                Err(e) => return Err(e),
            }
        }
    }

    /// Re-lists the namespaces (single-level) and their table names.
    pub async fn refresh(&self) -> Result<Vec<String>> {
        let namespaces = self.catalog.list_namespaces(None).await.map_err(ice)?;
        let mut names = Vec::new();
        for ns in namespaces {
            let name = ns.to_url_string();
            let tables = self.catalog.list_tables(&ns).await.map_err(ice)?;
            let schema = self.schema_for(&name);
            *schema.names.write().unwrap() = tables.iter().map(|t| t.name().to_string()).collect();
            names.push(name);
        }
        Ok(names)
    }

    fn schema_for(&self, name: &str) -> Arc<IcebergSchema> {
        if let Some(s) = self.schemas.read().unwrap().get(name) {
            return Arc::clone(s);
        }
        let mut schemas = self.schemas.write().unwrap();
        Arc::clone(schemas.entry(name.to_string()).or_insert_with(|| {
            Arc::new(IcebergSchema {
                catalog: Arc::clone(&self.catalog),
                namespace: NamespaceIdent::new(name.to_string()),
                names: RwLock::new(Vec::new()),
                tables: RwLock::new(HashMap::new()),
            })
        }))
    }
}

impl CatalogProvider for IcebergCatalogProvider {
    fn schema_names(&self) -> Vec<String> {
        self.schemas.read().unwrap().keys().cloned().collect()
    }

    /// Any name resolves; a namespace created after startup works without a restart,
    /// and an unknown one yields "table not found" from the planner.
    fn schema(&self, name: &str) -> Option<Arc<dyn SchemaProvider>> {
        Some(self.schema_for(name))
    }
}

#[derive(Debug)]
pub struct IcebergSchema {
    catalog: Arc<dyn Catalog>,
    namespace: NamespaceIdent,
    names: RwLock<Vec<String>>,
    tables: RwLock<HashMap<String, Arc<dyn TableProvider>>>,
}

#[async_trait]
impl SchemaProvider for IcebergSchema {
    fn table_names(&self) -> Vec<String> {
        self.names.read().unwrap().clone()
    }

    async fn table(&self, name: &str) -> Result<Option<Arc<dyn TableProvider>>> {
        if let Some(t) = self.tables.read().unwrap().get(name) {
            return Ok(Some(Arc::clone(t)));
        }
        let provider = match IcebergCatalogTableProvider::try_new(
            Arc::clone(&self.catalog),
            self.namespace.clone(),
            name,
            iceberg::Runtime::current(),
        )
        .await
        {
            Ok(p) => Arc::new(p) as Arc<dyn TableProvider>,
            Err(e) if is_not_found(&e) => return Ok(None),
            Err(e) => return Err(e),
        };
        self.tables
            .write()
            .unwrap()
            .insert(name.to_string(), Arc::clone(&provider));
        Ok(Some(provider))
    }

    fn table_exist(&self, name: &str) -> bool {
        self.names.read().unwrap().iter().any(|n| n == name)
            || self.tables.read().unwrap().contains_key(name)
    }
}
