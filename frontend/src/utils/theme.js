// Chart colours (SVG attributes need literal values, so they live here).
// The two series colours are a validated light-mode categorical pair (colour-blind
// separation and contrast checked on the white surface), assigned in fixed order.
export const CHART = {
  series1: '#2f6bff', // brand blue  - FairQueue, single-series charts
  series2: '#eb6834', // orange      - Global FIFO baseline
  soft: '#dbe5ff',    // pale bars in single-series charts; the highlighted bar uses series1
  grid: '#eef1f6',
  axis: '#e2e7ef',
  tick: '#6b7280',
  cursor: 'rgba(47, 107, 255, 0.06)',
};

export const MODE_COLOR = { FAIR_QUEUE: CHART.series1, GLOBAL_FIFO: CHART.series2 };
