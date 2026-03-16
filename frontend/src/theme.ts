import { createTheme, MantineColorsTuple } from '@mantine/core';

// BarnardHQ brand colors
const cyan: MantineColorsTuple = [
  '#e0fbff',
  '#b3f3ff',
  '#80eaff',
  '#4de1ff',
  '#1ad8ff',
  '#00d4ff', // primary [5]
  '#00aad4',
  '#0080a8',
  '#00577d',
  '#002f52',
];

const orange: MantineColorsTuple = [
  '#fff3e6',
  '#ffddb3',
  '#ffc780',
  '#ffb14d',
  '#ff9b1a',
  '#ff6b1a', // primary [5]
  '#d45616',
  '#a84212',
  '#7d2e0e',
  '#521b0a',
];

export const theme = createTheme({
  primaryColor: 'cyan',
  colors: {
    cyan,
    orange,
    dark: [
      '#e8edf2', // text primary [0]
      '#c4cdd8',
      '#9faabe',
      '#7a88a4',
      '#5a6478', // text muted [4]
      '#3a4258',
      '#1e2536',
      '#0e1117', // surface/card [7]
      '#090c10',
      '#050608', // background [9]
    ],
  },
  fontFamily: "'Rajdhani', sans-serif",
  fontSizes: {
    xs: '0.85rem',
    sm: '0.95rem',
    md: '1.05rem',
    lg: '1.2rem',
    xl: '1.4rem',
  },
  spacing: {
    xs: '0.65rem',
    sm: '0.85rem',
    md: '1.1rem',
    lg: '1.5rem',
    xl: '2rem',
  },
  headings: {
    fontFamily: "'Bebas Neue', sans-serif",
    sizes: {
      h1: { fontSize: '2.5rem', lineHeight: '1.2' },
      h2: { fontSize: '2rem', lineHeight: '1.2' },
      h3: { fontSize: '1.5rem', lineHeight: '1.3' },
    },
  },
  other: {
    monoFont: "'Share Tech Mono', monospace",
    brandCyan: '#00d4ff',
    brandOrange: '#ff6b1a',
    bgDeep: '#050608',
    bgPanel: '#0e1117',
    textPrimary: '#e8edf2',
    textMuted: '#5a6478',
  },
});
