import { Component, type ReactNode } from 'react';
import { Box, Button, Center, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  errorMessage: string;
  errorStack: string;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, errorMessage: '', errorStack: '' };

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      errorMessage: error?.message || 'Unknown error',
      errorStack: error?.stack || '',
    };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Error:', error?.message);
    console.error('[ErrorBoundary] Stack:', error?.stack);
    console.error('[ErrorBoundary] Component:', info.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, errorMessage: '', errorStack: '' });
  };

  render() {
    if (this.state.hasError) {
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
          <Center>
            <Stack align="center" gap="md">
              <IconAlertTriangle size={48} color="#ff6b6b" />
              <Title
                order={3}
                c="#e8edf2"
                style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
              >
                SOMETHING WENT WRONG
              </Title>
              <Text
                c="#5a6478"
                ta="center"
                maw={400}
                style={{ fontFamily: "'Share Tech Mono', monospace" }}
              >
                An unexpected error occurred. Try refreshing the page or navigating back.
              </Text>
              {this.state.errorMessage && (
                <Text
                  c="#ff6b6b"
                  ta="center"
                  maw={500}
                  size="xs"
                  style={{ fontFamily: "'Share Tech Mono', monospace", wordBreak: 'break-word' }}
                >
                  {this.state.errorMessage}
                </Text>
              )}
              <Button
                color="cyan"
                onClick={() => {
                  this.handleReset();
                  window.location.href = '/';
                }}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' } }}
              >
                RETURN TO DASHBOARD
              </Button>
            </Stack>
          </Center>
        </Box>
      );
    }

    return this.props.children;
  }
}
