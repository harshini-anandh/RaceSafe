// Chart colours (SVG attributes need literal values, so they live here).
// Series steps are the validated dark-mode categorical palette, in fixed order.
export const CHART = {
  series1: '#3987e5', // slot 1 (blue)   - Global FIFO / primary series
  series2: '#d95926', // slot 2 (orange) - FairQueue
  grid: '#22314f',
  axis: '#31456b',
  tick: '#a9b6cc',
  cursor: 'rgba(255,255,255,0.04)',
};

export const MODE_COLOR = { GLOBAL_FIFO: CHART.series1, FAIR_QUEUE: CHART.series2 };
