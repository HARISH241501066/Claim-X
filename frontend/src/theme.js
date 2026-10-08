// Colours for canvas and SVG drawing. They mirror the CSS tokens in index.css.
export const COLORS = {
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
}

// Node types use fixed categorical slots (blue, orange, aqua) so a type never changes colour.
export const NODE_COLORS = {
  provider: '#3987e5',
  facility: '#d95926',
  owner: '#199e70',
  member: '#898781',
  member_group: '#c3c2b7',
}

export const NODE_LABELS = {
  provider: 'Provider',
  facility: 'Facility',
  owner: 'Owner',
  member: 'Member',
  member_group: 'Member group',
}
