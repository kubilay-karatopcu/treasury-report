// Tabler design tokens — keep in sync with reference/static/css/styles.css.
export const theme = {
  colors: {
    primary:    '#206bc4',
    success:    '#2fb344',
    danger:     '#d63939',
    warning:    '#f76707',
    info:       '#4299e1',
    secondary:  '#616876',
    border:     '#e6e7e9',
    bgCard:     '#ffffff',
    bgPage:     '#f6f7f9',
    text:       '#1d273b',
    textMuted:  '#616876',
    textLight:  '#9aa0aa',
  },
  chart: {
    // Ordered palette for multi-series charts. Mirrors the FALLBACK_PALETTE
    // used in reference/static/js/competitor.js so the presentations module
    // looks consistent with the rest of Treasury Platform.
    palette: ['#206bc4', '#2fb344', '#f76707', '#ae3ec9', '#0ca678', '#d6336c',
              '#3bc9db', '#fcc419', '#4263eb', '#f06595'],
    // Soft grid line color — matches ApexCharts default neutrals in the platform.
    gridBorder: '#eef0f3',
    axisLabel:  '#9aa0aa',
  },
  radius: {
    sm: '4px',
    md: '6px',
    lg: '8px',
  },
};
