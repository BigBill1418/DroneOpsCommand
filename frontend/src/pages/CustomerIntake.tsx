import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Button,
  Card,
  Center,
  Checkbox,
  Group,
  Loader,
  Popover,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconAlertTriangle, IconArrowLeft, IconArrowRight, IconMapPin } from '@tabler/icons-react';
import { useParams } from 'react-router-dom';
import SignatureCanvas from 'react-signature-canvas';
import axios from 'axios';
import PdfViewer from '../components/PDFPreview/PdfViewer';
import { useBranding } from '../hooks/useBranding';

const US_STATES = [
  'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD',
  'MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC',
  'SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC',
];

const STATE_NAME_TO_ABBR: Record<string, string> = {
  Alabama:'AL',Alaska:'AK',Arizona:'AZ',Arkansas:'AR',California:'CA',Colorado:'CO',Connecticut:'CT',
  Delaware:'DE',Florida:'FL',Georgia:'GA',Hawaii:'HI',Idaho:'ID',Illinois:'IL',Indiana:'IN',Iowa:'IA',
  Kansas:'KS',Kentucky:'KY',Louisiana:'LA',Maine:'ME',Maryland:'MD',Massachusetts:'MA',Michigan:'MI',
  Minnesota:'MN',Mississippi:'MS',Missouri:'MO',Montana:'MT',Nebraska:'NE',Nevada:'NV',
  'New Hampshire':'NH','New Jersey':'NJ','New Mexico':'NM','New York':'NY','North Carolina':'NC',
  'North Dakota':'ND',Ohio:'OH',Oklahoma:'OK',Oregon:'OR',Pennsylvania:'PA','Rhode Island':'RI',
  'South Carolina':'SC','South Dakota':'SD',Tennessee:'TN',Texas:'TX',Utah:'UT',Vermont:'VT',
  Virginia:'VA',Washington:'WA','West Virginia':'WV',Wisconsin:'WI',Wyoming:'WY',
  'District of Columbia':'DC',
};

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
};

interface IntakeData {
  customer_name: string | null;
  customer_email: string | null;
  customer_phone: string | null;
  customer_address: string | null;
  customer_city: string | null;
  customer_state: string | null;
  customer_zip_code: string | null;
  customer_company: string | null;
  tos_pdf_url: string | null;
  already_completed: boolean;
}

interface NominatimResult {
  display_name: string;
  address?: {
    house_number?: string;
    road?: string;
    city?: string;
    town?: string;
    village?: string;
    state?: string;
    postcode?: string;
  };
}

type PageState = 'loading' | 'form' | 'completed' | 'already_done' | 'error' | 'expired';

export default function CustomerIntake() {
  const { token } = useParams<{ token: string }>();
  const [state, setState] = useState<PageState>('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('');
  const [stateVal, setStateVal] = useState('');
  const [zipCode, setZipCode] = useState('');
  const [company, setCompany] = useState('');
  const [tosAccepted, setTosAccepted] = useState(false);
  const [tosPdfUrl, setTosPdfUrl] = useState<string | null>(null);
  const [tosPdfBlobUrl, setTosPdfBlobUrl] = useState<string | null>(null);

  // Address autofill (Nominatim / OpenStreetMap)
  const [addressSuggestions, setAddressSuggestions] = useState<NominatimResult[]>([]);
  const [addressLoading, setAddressLoading] = useState(false);
  const [addressPopover, setAddressPopover] = useState(false);
  const addressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sigRef = useRef<SignatureCanvas>(null);
  const branding = useBranding();

  const searchAddress = useCallback((query: string) => {
    if (addressTimerRef.current) clearTimeout(addressTimerRef.current);
    if (query.length < 4) {
      setAddressSuggestions([]);
      setAddressPopover(false);
      return;
    }
    addressTimerRef.current = setTimeout(async () => {
      setAddressLoading(true);
      try {
        const resp = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&addressdetails=1&limit=5&countrycodes=us&q=${encodeURIComponent(query)}`,
          { headers: { 'Accept-Language': 'en' } }
        );
        const data: NominatimResult[] = await resp.json();
        setAddressSuggestions(data);
        setAddressPopover(data.length > 0);
      } catch {
        setAddressSuggestions([]);
      } finally {
        setAddressLoading(false);
      }
    }, 400);
  }, []);

  const selectAddress = (result: NominatimResult) => {
    const addr = result.address;
    if (addr) {
      const street = [addr.house_number, addr.road].filter(Boolean).join(' ');
      if (street) setAddress(street);
      setCity(addr.city || addr.town || addr.village || '');
      // Convert full state name to abbreviation for the dropdown
      const rawState = addr.state || '';
      setStateVal(STATE_NAME_TO_ABBR[rawState] || rawState);
      setZipCode(addr.postcode || '');
    } else {
      setAddress(result.display_name);
    }
    setAddressPopover(false);
  };

  useEffect(() => {
    if (!token) { setState('error'); setErrorMsg('Invalid link'); return; }

    axios.get<IntakeData>(`/api/intake/form/${token}`)
      .then((r) => {
        const d = r.data;
        if (d.already_completed) {
          setState('already_done');
          return;
        }
        setName(d.customer_name || '');
        setEmail(d.customer_email || '');
        setPhone(d.customer_phone || '');
        setAddress(d.customer_address || '');
        setCity(d.customer_city || '');
        setStateVal(d.customer_state || '');
        setZipCode(d.customer_zip_code || '');
        setCompany(d.customer_company || '');
        setTosPdfUrl(d.tos_pdf_url);
        setState('form');
      })
      .catch((err) => {
        if (err.response?.status === 410) {
          setState('expired');
        } else if (err.response?.status === 404) {
          setState('error');
          setErrorMsg('This link is invalid or has expired. Please request a new one.');
        } else if (!err.response) {
          setState('error');
          setErrorMsg('Unable to reach the server. If this keeps happening, try clearing your browser cookies and revisiting the link.');
          console.error('[Intake] Network/parse error:', err.message);
        } else {
          setState('error');
          setErrorMsg(err.response?.data?.detail || 'Something went wrong. Please try again.');
        }
      });
  }, [token]);

  // Fetch the TOS PDF as a blob for inline display when entering step 2
  useEffect(() => {
    if (step === 2 && tosPdfUrl && !tosPdfBlobUrl) {
      axios.get(tosPdfUrl, { responseType: 'blob' })
        .then((r) => {
          const blob = new Blob([r.data], { type: 'application/pdf' });
          setTosPdfBlobUrl(URL.createObjectURL(blob));
        })
        .catch(() => {
          console.error('[Intake] Failed to load TOS PDF');
        });
    }
  }, [step, tosPdfUrl, tosPdfBlobUrl]);

  // Clean up blob URL on unmount
  useEffect(() => {
    return () => {
      if (tosPdfBlobUrl) URL.revokeObjectURL(tosPdfBlobUrl);
    };
  }, [tosPdfBlobUrl]);

  const handleNext = () => {
    if (!name.trim() || !email.trim()) {
      notifications.show({ title: 'Required', message: 'Name and email are required.', color: 'red' });
      return;
    }
    if (!address.trim() || !city.trim() || !stateVal.trim() || !zipCode.trim()) {
      notifications.show({ title: 'Required', message: 'Full mailing address is required (street, city, state, zip).', color: 'red' });
      return;
    }
    setStep(2);
    // Scroll to top of card
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!tosAccepted) {
      notifications.show({ title: 'Required', message: 'You must agree to the Terms of Service.', color: 'red' });
      return;
    }
    if (sigRef.current?.isEmpty()) {
      notifications.show({ title: 'Required', message: 'Please provide your signature.', color: 'red' });
      return;
    }

    const signatureData = sigRef.current?.toDataURL('image/png') || '';

    setSubmitting(true);
    try {
      await axios.post(`/api/intake/form/${token}`, {
        name: name.trim(),
        email: email.trim(),
        phone: phone.trim() || null,
        address: address.trim() || null,
        city: city.trim() || null,
        state: stateVal.trim() || null,
        zip_code: zipCode.trim() || null,
        company: company.trim() || null,
        signature_data: signatureData,
        tos_accepted: true,
      });
      setState('completed');
    } catch (err: any) {
      notifications.show({
        title: 'Error',
        message: err.response?.data?.detail || 'Submission failed. Please try again.',
        color: 'red',
      });
    } finally {
      setSubmitting(false);
    }
  };

  const renderContent = () => {
    if (state === 'loading') {
      return (
        <Center py={60}>
          <Loader color="cyan" size="lg" />
        </Center>
      );
    }

    if (state === 'error') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconAlertTriangle size={48} color="#ff6b6b" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>INVALID LINK</Title>
          <Text c="#5a6478" ta="center">{errorMsg}</Text>
        </Stack>
      );
    }

    if (state === 'expired') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconAlertTriangle size={48} color="#ff6b1a" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>LINK EXPIRED</Title>
          <Text c="#5a6478" ta="center">This intake link has expired. Please contact us for a new one.</Text>
        </Stack>
      );
    }

    if (state === 'already_done') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconCheck size={48} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>ALREADY COMPLETED</Title>
          <Text c="#5a6478" ta="center">You have already submitted your information. Thank you!</Text>
        </Stack>
      );
    }

    if (state === 'completed') {
      return (
        <Stack align="center" gap="md" py={40}>
          <IconCheck size={48} color="#00ff88" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>THANK YOU!</Title>
          <Text c="#5a6478" ta="center" maw={400}>
            Your information has been submitted and your Terms of Service agreement has been signed.
            Our team will be in touch.
          </Text>
        </Stack>
      );
    }

    // ── Step 1: Customer Information ──
    if (step === 1) {
      return (
        <Stack gap="md">
          {/* Step indicator */}
          <Group justify="center" gap="xs">
            <Box
              style={{
                width: 28, height: 28, borderRadius: '50%',
                background: '#00d4ff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: "'Share Tech Mono', monospace", fontSize: 13, fontWeight: 700, color: '#050608',
              }}
            >1</Box>
            <Box style={{ width: 40, height: 2, background: '#1a1f2e' }} />
            <Box
              style={{
                width: 28, height: 28, borderRadius: '50%',
                border: '1px solid #1a1f2e', display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: "'Share Tech Mono', monospace", fontSize: 13, color: '#5a6478',
              }}
            >2</Box>
          </Group>

          <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
            CUSTOMER INFORMATION
          </Text>

          <TextInput label="Full Name" required value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" styles={inputStyles} />
          <TextInput label="Email" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" styles={inputStyles} />
          <TextInput label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} autoComplete="tel" styles={inputStyles} />
          <TextInput label="Company" value={company} onChange={(e) => setCompany(e.target.value)} autoComplete="organization" styles={inputStyles} />

          <Text c="#5a6478" size="sm" mt="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
            MAILING ADDRESS *
          </Text>

          <Popover opened={addressPopover} onClose={() => setAddressPopover(false)} position="bottom-start" width="target">
            <Popover.Target>
              <TextInput
                label="Street Address"
                required
                autoComplete="address-line1"
                leftSection={addressLoading ? <Loader size={14} color="cyan" /> : <IconMapPin size={14} />}
                value={address}
                onChange={(e) => {
                  setAddress(e.target.value);
                  searchAddress(e.target.value);
                }}
                onFocus={() => { if (addressSuggestions.length > 0) setAddressPopover(true); }}
                styles={inputStyles}
              />
            </Popover.Target>
            <Popover.Dropdown style={{ background: '#0e1117', border: '1px solid #1a1f2e', padding: 0, maxHeight: 200, overflow: 'auto' }}>
              {addressSuggestions.map((s, i) => (
                <Text
                  key={i}
                  size="sm"
                  c="#e8edf2"
                  p="xs"
                  style={{ cursor: 'pointer', borderBottom: '1px solid #1a1f2e' }}
                  onMouseDown={(e) => { e.preventDefault(); }}
                  onClick={() => selectAddress(s)}
                >
                  {s.display_name}
                </Text>
              ))}
            </Popover.Dropdown>
          </Popover>

          <Group grow>
            <TextInput label="City" required value={city} onChange={(e) => setCity(e.target.value)} autoComplete="address-level2" styles={inputStyles} />
            <Select
              label="State"
              required
              data={US_STATES}
              value={stateVal || null}
              onChange={(val) => setStateVal(val || '')}
              searchable
              styles={{
                input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
                label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px', letterSpacing: '1px' },
                dropdown: { background: '#0e1117', borderColor: '#1a1f2e' },
                option: { color: '#e8edf2', '&[data-selected]': { background: '#00d4ff' } },
              }}
            />
            <TextInput label="Zip Code" required value={zipCode} onChange={(e) => setZipCode(e.target.value)} autoComplete="postal-code" styles={inputStyles} />
          </Group>

          <Button
            color="cyan"
            fullWidth
            size="lg"
            mt="md"
            rightSection={<IconArrowRight size={18} />}
            onClick={handleNext}
            styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '18px' } }}
          >
            NEXT
          </Button>
        </Stack>
      );
    }

    // ── Step 2: TOS Review + Signature ──
    return (
      <form onSubmit={handleSubmit}>
        <Stack gap="md">
          {/* Step indicator */}
          <Group justify="center" gap="xs">
            <Box
              style={{
                width: 28, height: 28, borderRadius: '50%',
                border: '1px solid #00d4ff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: "'Share Tech Mono', monospace", fontSize: 13, color: '#00d4ff',
              }}
            >
              <IconCheck size={14} />
            </Box>
            <Box style={{ width: 40, height: 2, background: '#00d4ff' }} />
            <Box
              style={{
                width: 28, height: 28, borderRadius: '50%',
                background: '#00d4ff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: "'Share Tech Mono', monospace", fontSize: 13, fontWeight: 700, color: '#050608',
              }}
            >2</Box>
          </Group>

          <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
            TERMS OF SERVICE
          </Text>

          {/* TOS PDF viewer */}
          {tosPdfUrl && tosPdfBlobUrl ? (
            <PdfViewer
              url={tosPdfBlobUrl}
              height={500}
              downloadFilename="Terms_of_Service.pdf"
            />
          ) : tosPdfUrl ? (
            <Center py={40} style={{ background: '#050608', border: '1px solid #1a1f2e', borderRadius: 6 }}>
              <Loader color="cyan" size="sm" />
              <Text c="#5a6478" size="sm" ml="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Loading document...
              </Text>
            </Center>
          ) : (
            <Box
              p="md"
              style={{
                border: '1px solid #1a1f2e',
                borderRadius: 6,
                background: '#050608',
              }}
            >
              <Text c="#5a6478" size="sm" ta="center">
                Terms of Service document not yet available.
              </Text>
            </Box>
          )}

          <Checkbox
            label="I have read and agree to the Terms of Service"
            checked={tosAccepted}
            onChange={(e) => setTosAccepted(e.currentTarget.checked)}
            color="cyan"
            styles={{
              label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace", fontSize: '13px' },
            }}
          />

          {/* Signature */}
          <div style={{ marginTop: 8 }}>
            <Text c="#5a6478" size="sm" mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '1px' }}>
              DIGITAL SIGNATURE
            </Text>
            <Box
              style={{
                border: '1px solid #1a1f2e',
                borderRadius: 6,
                background: '#050608',
                overflow: 'hidden',
              }}
            >
              <SignatureCanvas
                ref={sigRef}
                penColor="#00d4ff"
                backgroundColor="#050608"
                canvasProps={{
                  style: { width: '100%', height: 150 },
                }}
              />
            </Box>
            <Group justify="space-between" mt={4}>
              <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                Sign above using your mouse or finger
              </Text>
              <Button
                variant="subtle"
                color="gray"
                size="xs"
                onClick={() => sigRef.current?.clear()}
                styles={{ root: { fontFamily: "'Share Tech Mono', monospace" } }}
              >
                Clear
              </Button>
            </Group>
          </div>

          <Group grow mt="md">
            <Button
              variant="subtle"
              color="gray"
              size="lg"
              leftSection={<IconArrowLeft size={18} />}
              onClick={() => setStep(1)}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '18px' } }}
            >
              BACK
            </Button>
            <Button
              type="submit"
              color="cyan"
              size="lg"
              loading={submitting}
              styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px', fontSize: '18px' } }}
            >
              SUBMIT & SIGN
            </Button>
          </Group>
        </Stack>
      </form>
    );
  };

  return (
    <Box
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <Card
        shadow="xl"
        padding="xl"
        radius="md"
        w="100%"
        maw={600}
        style={{
          background: '#0e1117',
          border: '1px solid #1a1f2e',
        }}
      >
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

          {renderContent()}
        </Stack>
      </Card>
    </Box>
  );
}
