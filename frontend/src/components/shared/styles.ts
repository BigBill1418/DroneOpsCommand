export const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
};

export const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

export const monoFont = { fontFamily: "'Share Tech Mono', monospace" };

export const statusColors: Record<string, string> = {
  draft: 'gray',
  scheduled: 'blue',
  in_progress: 'yellow',
  processing: 'orange',
  review: 'cyan',
  delivered: 'green',
  completed: 'green',
  sent: 'teal',
};
