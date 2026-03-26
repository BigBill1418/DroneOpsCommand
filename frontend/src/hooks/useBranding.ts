import { useEffect, useState } from 'react';
import axios from 'axios';

export interface Branding {
  company_name: string;
  company_tagline: string;
  company_website: string;
  company_social_url: string;
  company_contact_email: string;
  company_logo: string;
  brand_primary_color: string;
  brand_accent_color: string;
}

const DEFAULTS: Branding = {
  company_name: 'DroneOps',
  company_tagline: 'Professional Aerial Operations',
  company_website: '',
  company_social_url: '',
  company_contact_email: '',
  company_logo: '',
  brand_primary_color: '#050608',
  brand_accent_color: '#00d4ff',
};

let cachedBranding: Branding | null = null;
let fetchPromise: Promise<Branding> | null = null;

async function fetchBranding(): Promise<Branding> {
  try {
    const r = await axios.get<Branding>('/api/branding');
    cachedBranding = { ...DEFAULTS, ...r.data };
    // Use defaults for empty strings
    if (!cachedBranding.company_name) cachedBranding.company_name = DEFAULTS.company_name;
    if (!cachedBranding.company_tagline) cachedBranding.company_tagline = DEFAULTS.company_tagline;
    return cachedBranding;
  } catch {
    cachedBranding = DEFAULTS;
    return DEFAULTS;
  }
}

export function useBranding(): Branding {
  const [branding, setBranding] = useState<Branding>(cachedBranding || DEFAULTS);

  useEffect(() => {
    if (cachedBranding) {
      setBranding(cachedBranding);
      return;
    }
    if (!fetchPromise) {
      fetchPromise = fetchBranding();
    }
    fetchPromise.then(setBranding);
  }, []);

  return branding;
}

/** Call this to clear the cache (e.g. after saving branding settings). */
export function invalidateBrandingCache() {
  cachedBranding = null;
  fetchPromise = null;
}
