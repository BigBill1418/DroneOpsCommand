import { useEffect, useState } from 'react';
import api from '../api/client';

let cachedDemoMode: boolean | null = null;

export function useDemoMode(): boolean {
  const [isDemo, setIsDemo] = useState(cachedDemoMode ?? false);

  useEffect(() => {
    if (cachedDemoMode !== null) return;
    api.get('/demo/status')
      .then((r) => {
        cachedDemoMode = r.data.demo_mode;
        setIsDemo(r.data.demo_mode);
      })
      .catch(() => {
        cachedDemoMode = false;
      });
  }, []);

  return isDemo;
}
