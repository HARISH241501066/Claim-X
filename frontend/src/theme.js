// Colours for canvas and SVG drawing, per theme. They mirror the CSS tokens in index.css.
export const PALETTES = {
  dark: {
    page: '#0d0d0d',
    surface: '#1a1a19',
    line: '#2c2c2a',
    axis: '#383835',
    ink: '#ffffff',
    ink2: '#c3c2b7',
    muted: '#898781',
    accent: '#3987e5',
    accentDim: '#184f95',
    flag: '#e66767',
    link: '#4a4a46',
    cursor: 'rgba(255,255,255,0.04)',
  },
  light: {
    page: '#f4f5f8',
    surface: '#ffffff',
    line: '#e1e4ea',
    axis: '#c4c9d3',
    ink: '#11141a',
    ink2: '#41464f',
    muted: '#666d7a',
    accent: '#1d62d4',
    accentDim: '#a9c4f0',
    flag: '#c62828',
    link: '#b6bcc8',
    cursor: 'rgba(17,20,26,0.05)',
  },
}

// Node types use fixed categorical slots (blue, orange, aqua) so a type never changes colour.
export const NODE_PALETTES = {
  dark: {
    provider: '#3987e5',
    facility: '#d95926',
    owner: '#199e70',
    member: '#898781',
    member_group: '#c3c2b7',
  },
  light: {
    provider: '#1d62d4',
    facility: '#c2410c',
    owner: '#0b8660',
    member: '#7b8290',
    member_group: '#41464f',
  },
}

// Dark values stay the defaults for anything drawn outside a ThemeProvider.
export const COLORS = PALETTES.dark
export const NODE_COLORS = NODE_PALETTES.dark

export const NODE_LABELS = {
  provider: 'Provider',
  facility: 'Facility',
  owner: 'Owner',
  member: 'Member',
  member_group: 'Member group',
}
