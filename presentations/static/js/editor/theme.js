// Design tokens for the React editor — Phase 11.dark-editor.
//
// Two palettes share the same shape so swapping themes is a one-line
// assignment. Today the editor always boots with `dark`; Phase 11.light
// will read `document.documentElement.dataset.theme` and pick the
// corresponding object.
//
// Chart palette uses the PRISMA accent colors so charts read on dark
// without inverting (gold/blue/green/red on dark = readable contrast).

const DARK = {
  colors: {
    primary:    '#6B8AFD',  // --liq
    success:    '#4A9B6E',  // --green
    danger:     '#B85450',  // --red
    warning:    '#D4A656',  // --gold
    info:       '#6B8AFD',
    secondary:  '#A8B2C4',  // --ink-mute
    border:     '#2A3043',  // --border
    bgCard:     '#0E1320',  // --bg-1
    bgPage:     '#0A0E1A',  // --bg-0
    text:       '#E8ECF1',  // --ink
    textMuted:  '#A8B2C4',  // --ink-mute
    textLight:  '#6B768A',  // --ink-faint
  },
  chart: {
    // PRISMA-tuned palette — saturated enough to read on dark, ordered
    // so the first 2 colors (gold + blue) match the deck's anchor.
    palette: ['#D4A656', '#6B8AFD', '#4A9B6E', '#B85450', '#E8C190',
              '#B6BECD', '#9C7DE0', '#4FC3F7', '#FFB74D', '#F06292'],
    gridBorder: '#1F2433',   // --border-soft
    axisLabel:  '#A8B2C4',   // --ink-mute
    foreColor:  '#C8D0DD',   // --ink-soft (ApexCharts global text)
    tooltipBg:  '#131826',   // --bg-2
  },
  radius: { sm: '4px', md: '6px', lg: '8px' },
};

const LIGHT = {
  // Phase 11.light placeholder — flip these when the toggle ships.
  // Today the editor always picks DARK regardless of dataset.theme.
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
    palette: ['#206bc4', '#2fb344', '#f76707', '#ae3ec9', '#0ca678', '#d6336c',
              '#3bc9db', '#fcc419', '#4263eb', '#f06595'],
    gridBorder: '#eef0f3',
    axisLabel:  '#9aa0aa',
    foreColor:  '#1d273b',
    tooltipBg:  '#ffffff',
  },
  radius: { sm: '4px', md: '6px', lg: '8px' },
};

// One source of truth for the running theme. Defaults to dark.
function pickTheme() {
  if (typeof document !== 'undefined') {
    const t = document.documentElement.dataset.theme;
    if (t === 'light') return LIGHT;
  }
  return DARK;
}

export const theme = pickTheme();
export { DARK, LIGHT };
