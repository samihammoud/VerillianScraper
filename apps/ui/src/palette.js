// Vector Field instrument accents — confined to hairlines and points, never
// fields. One saturated hue per world, all keyed to the same mid-value/
// mid-chroma so no world's basin outweighs another's by color alone.
export const CHANNELS = [
  "#2F6FED", // field blue
  "#E0692F", // basin orange
  "#1F9E6E", // signal green
  "#B0399B", // magenta channel
  "#C99A1E", // amber channel
  "#5B58D6", // violet channel
  "#1C8C9E", // cyan channel
  "#C44545", // red channel
];

export function colorFor(index) {
  return CHANNELS[index % CHANNELS.length];
}
