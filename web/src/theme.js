// Design tokens. Dark base is from design_reference/ (Ocean theme); the accent
// is GOLD to match the product name "GOLD" — a muted, premium brushed gold, not
// neon yellow. Accent is decorative only (logo mark, primary button, focus
// rings, active tab/filter, thin accent lines). grn/red are FUNCTIONAL — they
// signal gains/losses on returns and must never be repurposed as accents.
export const theme = {
  bg: '#0A1120',
  surf: '#101B30',
  surf2: '#0D1626',
  bd: '#1D2B47',
  txt: '#E8EEF9',
  mut: '#8DA0C4',
  grn: '#2BD980', // functional: gains — do not reuse as accent
  red: '#FF5A66', // functional: losses — do not reuse as accent
  acc: '#D4AF37', // gold — primary accent
  accHover: '#E6C34E', // gold — brighter, for hover states
  accSoft: 'rgba(212,175,55,.12)', // gold @ low alpha — glow / active-tab bg
  accDim: 'rgba(212,175,55,.35)', // desaturated gold — thin accent lines/borders
  onAcc: '#1A1405', // dark ink for text/icons on a gold fill (contrast)
};
