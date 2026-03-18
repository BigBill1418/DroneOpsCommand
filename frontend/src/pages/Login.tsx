import { useState } from 'react';
import {
  Box,
  Button,
  Card,
  Center,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useBranding } from '../hooks/useBranding';

interface LoginProps {
  onLogin: (username: string, password: string) => Promise<void>;
}

export default function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const branding = useBranding();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await onLogin(username, password);
    } catch {
      notifications.show({
        title: 'Login Failed',
        message: 'Invalid credentials',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w={400}
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
        }}
      >
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <Center>
              <div>
                <Title
                  order={1}
                  style={{
                    fontFamily: "'Bebas Neue', sans-serif",
                    letterSpacing: '4px',
                    fontSize: '36px',
                    textAlign: 'center',
                  }}
                  c="#e8edf2"
                >
                  {branding.company_name.toUpperCase()}
                </Title>
                <Text
                  ta="center"
                  size="xs"
                  c="#5a6478"
                  style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '3px' }}
                >
                  {branding.company_tagline.toUpperCase()}
                </Text>
              </div>
            </Center>

            <TextInput
              label="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <PasswordInput
              label="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
              }}
            />

            <Button
              type="submit"
              fullWidth
              loading={loading}
              color="cyan"
              variant="filled"
              styles={{
                root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '16px' },
              }}
            >
              LOGIN
            </Button>
          </Stack>
        </form>
      </Card>
    </Box>
  );
}
