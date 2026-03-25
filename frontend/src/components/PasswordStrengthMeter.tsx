import { Box, Text } from '@mantine/core';

const RULES = [
  { label: 'At least 10 characters', test: (p: string) => p.length >= 10 },
  { label: 'Uppercase letter', test: (p: string) => /[A-Z]/.test(p) },
  { label: 'Lowercase letter', test: (p: string) => /[a-z]/.test(p) },
  { label: 'Number', test: (p: string) => /\d/.test(p) },
  { label: 'Special character (!@#$%^&*...)', test: (p: string) => /[^A-Za-z0-9]/.test(p) },
];

interface Props {
  password: string;
}

export default function PasswordStrengthMeter({ password }: Props) {
  const passed = RULES.filter((r) => r.test(password)).length;
  const total = RULES.length;
  const ratio = password.length === 0 ? 0 : passed / total;

  // Red → Orange → Yellow → Green gradient based on ratio
  const getBarColor = () => {
    if (ratio <= 0.2) return '#ff4444';
    if (ratio <= 0.4) return '#ff8800';
    if (ratio <= 0.6) return '#ffbb00';
    if (ratio <= 0.8) return '#88cc00';
    return '#00cc66';
  };

  const getCheckColor = (met: boolean) => (met ? '#00cc66' : '#5a6478');

  if (password.length === 0) return null;

  return (
    <Box mt={4}>
      {/* Overall strength bar */}
      <Box
        style={{
          height: 4,
          borderRadius: 2,
          background: '#1a1f2e',
          overflow: 'hidden',
          marginBottom: 8,
        }}
      >
        <Box
          style={{
            height: '100%',
            width: `${ratio * 100}%`,
            background: getBarColor(),
            borderRadius: 2,
            transition: 'width 0.2s ease, background 0.2s ease',
          }}
        />
      </Box>

      {/* Individual rules */}
      <Box style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {RULES.map((rule) => {
          const met = rule.test(password);
          return (
            <Text
              key={rule.label}
              size="xs"
              style={{
                fontFamily: "'Share Tech Mono', monospace",
                fontSize: 10,
                letterSpacing: '0.5px',
                color: getCheckColor(met),
                transition: 'color 0.2s ease',
              }}
            >
              {met ? '\u2713' : '\u2717'} {rule.label}
            </Text>
          );
        })}
      </Box>

      {/* Summary label */}
      <Text
        size="xs"
        mt={4}
        fw={700}
        style={{
          fontFamily: "'Share Tech Mono', monospace",
          fontSize: 10,
          letterSpacing: '1px',
          color: getBarColor(),
          transition: 'color 0.2s ease',
        }}
      >
        {passed === total
          ? 'PASSWORD MEETS ALL REQUIREMENTS'
          : `${passed}/${total} REQUIREMENTS MET`}
      </Text>
    </Box>
  );
}

/** Utility: check if password meets all rules (for form validation) */
export function isPasswordValid(password: string): boolean {
  return RULES.every((r) => r.test(password));
}

/** Utility: get list of unmet rules (for error messages) */
export function getPasswordErrors(password: string): string[] {
  return RULES.filter((r) => !r.test(password)).map((r) => r.label);
}
