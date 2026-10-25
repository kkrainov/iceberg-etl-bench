// Licensed under the Apache License, Version 2.0 (see LICENSE in the repository root).

//! Memory pool wrapper that records the pool's high-water mark.
//!
//! The inner pool is what DataFusion itself builds for a memory limit
//! (`RuntimeEnvBuilder::with_memory_limit`): `TrackConsumersPool<GreedyMemoryPool>`.
//! This wrapper only observes `reserved()` after every successful grow, so the
//! accounting and the `ResourcesExhausted` errors are DataFusion's own.

use std::fmt;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use datafusion::common::Result;
use datafusion::execution::memory_pool::{
    MemoryConsumer, MemoryLimit, MemoryPool, MemoryReservation,
};

#[derive(Debug)]
pub struct PeakPool {
    inner: Arc<dyn MemoryPool>,
    peak: AtomicUsize,
}

impl PeakPool {
    pub fn new(inner: Arc<dyn MemoryPool>) -> Self {
        Self {
            inner,
            peak: AtomicUsize::new(0),
        }
    }

    /// Highest `reserved()` seen since the last reset.
    pub fn peak(&self) -> usize {
        self.peak.load(Ordering::Relaxed)
    }

    /// Starts a new high-water period at the current reservation; returns it.
    pub fn reset_peak(&self) -> usize {
        let current = self.inner.reserved();
        self.peak.store(current, Ordering::Relaxed);
        current
    }

    pub fn limit(&self) -> Option<usize> {
        match self.inner.memory_limit() {
            MemoryLimit::Finite(n) => Some(n),
            _ => None,
        }
    }

    pub fn inner_name(&self) -> String {
        self.inner.name().to_string()
    }

    fn observe(&self) {
        self.peak.fetch_max(self.inner.reserved(), Ordering::Relaxed);
    }
}

impl fmt::Display for PeakPool {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "PeakPool({})", self.inner)
    }
}

impl MemoryPool for PeakPool {
    fn name(&self) -> &str {
        "peak_pool"
    }

    fn register(&self, consumer: &MemoryConsumer) {
        self.inner.register(consumer)
    }

    fn unregister(&self, consumer: &MemoryConsumer) {
        self.inner.unregister(consumer)
    }

    fn grow(&self, reservation: &MemoryReservation, additional: usize) {
        self.inner.grow(reservation, additional);
        self.observe();
    }

    fn shrink(&self, reservation: &MemoryReservation, shrink: usize) {
        self.inner.shrink(reservation, shrink)
    }

    fn try_grow(&self, reservation: &MemoryReservation, additional: usize) -> Result<()> {
        self.inner.try_grow(reservation, additional)?;
        self.observe();
        Ok(())
    }

    fn reserved(&self) -> usize {
        self.inner.reserved()
    }

    fn memory_limit(&self) -> MemoryLimit {
        self.inner.memory_limit()
    }
}
