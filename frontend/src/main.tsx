import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { BrowserRouter } from 'react-router-dom';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/dropzone/styles.css';
import '@mantine/dates/styles.css';
import '@mantine/tiptap/styles.css';
import './mobile.css';
import { initFrontendSentry } from './lib/sentry';
import { theme } from './theme';
import App from './App';

// Phase-5 observability — no-op unless VITE_SENTRY_DSN was set at build
// time. Must run before createRoot so the browser client hooks React's
// error boundaries.
initFrontendSentry();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </MantineProvider>
  </React.StrictMode>
);
