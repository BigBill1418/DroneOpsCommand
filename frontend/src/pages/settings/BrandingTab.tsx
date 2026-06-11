import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Button,
  Card,
  Group,
  Image,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconPalette, IconPhoto, IconTrash, IconWorldWww } from '@tabler/icons-react';
import api from '../../api/client';
import { inputStyles, cardStyle } from '../../components/shared/styles';
import { invalidateBrandingCache } from '../../hooks/useBranding';

/**
 * BRANDING tab — company name/colors/logo. Default Settings tab, so this is
 * the only subtree that fetches on the page's initial mount (1 GET, down from
 * the old 19-GET burst). Field names and the PUT/POST payloads are unchanged.
 */
export default function BrandingTab() {
  const [brandingSaving, setBrandingSaving] = useState(false);
  const [companyLogo, setCompanyLogo] = useState<string>('');
  const [logoUploading, setLogoUploading] = useState(false);

  const brandingForm = useForm({
    initialValues: {
      company_name: '',
      company_tagline: '',
      company_website: '',
      company_social_url: '',
      company_contact_email: '',
      brand_primary_color: '#050608',
      brand_accent_color: '#00d4ff',
    },
  });

  useEffect(() => {
    api.get('/settings/branding')
      .then((r) => { brandingForm.setValues(r.data); setCompanyLogo(r.data.company_logo || ''); })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveBranding = async (values: typeof brandingForm.values) => {
    setBrandingSaving(true);
    try {
      await api.put('/settings/branding', values);
      invalidateBrandingCache();
      notifications.show({ title: 'Saved', message: 'Branding settings updated — reload to see changes in header', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to save branding settings', color: 'red' });
    } finally {
      setBrandingSaving(false);
    }
  };

  return (
    <Stack gap="md">
      <Card padding="lg" radius="md" style={cardStyle}>
        <Group gap="sm" mb="md">
          <IconPalette size={20} color="#00d4ff" />
          <Title order={3} c="#e8edf2" style={{ letterSpacing: '1px' }}>COMPANY BRANDING</Title>
        </Group>
        <Text c="#5a6478" size="xs" mb="md" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          These settings control how your company appears throughout the app, in emails, PDF reports, and customer-facing pages.
        </Text>
        {/* Logo Upload */}
        <Text size="sm" fw={600} c="#e8edf2" mb={4}>Company Logo</Text>
        <Text size="xs" c="#5a6478" mb="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
          Used in PDF reports and email headers. Recommended: PNG with transparent background, max 400px wide.
        </Text>
        <Group gap="md" mb="md">
          {companyLogo ? (
            <Group gap="sm" align="center">
              <Image src={`/uploads/${companyLogo}`} h={60} fit="contain" radius="sm" style={{ background: '#1a1f2e', padding: 8, borderRadius: 6 }} />
              <ActionIcon
                variant="light"
                color="red"
                size="sm"
                onClick={async () => {
                  try {
                    await api.delete('/settings/branding/logo');
                    setCompanyLogo('');
                    notifications.show({ title: 'Deleted', message: 'Logo removed', color: 'cyan' });
                  } catch {
                    notifications.show({ title: 'Error', message: 'Failed to delete logo', color: 'red' });
                  }
                }}
              >
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
          ) : (
            <Text size="xs" c="#5a6478" fs="italic">No logo uploaded</Text>
          )}
          <Button
            size="xs"
            variant="light"
            color="cyan"
            leftSection={<IconPhoto size={14} />}
            loading={logoUploading}
            onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = 'image/png,image/jpeg,image/webp,image/svg+xml';
              input.onchange = async (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (!file) return;
                setLogoUploading(true);
                try {
                  const formData = new FormData();
                  formData.append('file', file);
                  const resp = await api.post('/settings/branding/logo', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
                  setCompanyLogo(resp.data.company_logo);
                  invalidateBrandingCache();
                  notifications.show({ title: 'Uploaded', message: 'Company logo saved', color: 'cyan' });
                } catch {
                  notifications.show({ title: 'Error', message: 'Failed to upload logo', color: 'red' });
                } finally {
                  setLogoUploading(false);
                }
              };
              input.click();
            }}
          >
            {companyLogo ? 'Replace Logo' : 'Upload Logo'}
          </Button>
        </Group>

        <form onSubmit={brandingForm.onSubmit(handleSaveBranding)}>
          <Stack gap="sm">
            <TextInput
              label="Company Name"
              placeholder="Your Company Name"
              {...brandingForm.getInputProps('company_name')}
              styles={inputStyles}
            />
            <TextInput
              label="Tagline"
              placeholder="Professional Aerial Operations"
              {...brandingForm.getInputProps('company_tagline')}
              styles={inputStyles}
            />
            <TextInput
              label="Website"
              placeholder="https://yourcompany.com"
              leftSection={<IconWorldWww size={14} />}
              {...brandingForm.getInputProps('company_website')}
              styles={inputStyles}
            />
            <TextInput
              label="Social Media URL"
              placeholder="https://facebook.com/yourcompany"
              {...brandingForm.getInputProps('company_social_url')}
              styles={inputStyles}
            />
            <TextInput
              label="Contact Email"
              placeholder="info@yourcompany.com"
              {...brandingForm.getInputProps('company_contact_email')}
              styles={inputStyles}
            />

            {/* Brand Colors */}
            <Text size="sm" fw={600} c="#e8edf2" mt="xs">PDF Report Colors</Text>
            <Group gap="md">
              <div>
                <Text size="xs" c="#5a6478" mb={4}>Header Background</Text>
                <Group gap="xs">
                  <input
                    type="color"
                    value={brandingForm.values.brand_primary_color || '#050608'}
                    onChange={(e) => brandingForm.setFieldValue('brand_primary_color', e.target.value)}
                    style={{ width: 36, height: 36, border: '1px solid #1a1f2e', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 2 }}
                  />
                  <TextInput
                    size="xs"
                    w={90}
                    {...brandingForm.getInputProps('brand_primary_color')}
                    styles={inputStyles}
                  />
                </Group>
              </div>
              <div>
                <Text size="xs" c="#5a6478" mb={4}>Accent Color</Text>
                <Group gap="xs">
                  <input
                    type="color"
                    value={brandingForm.values.brand_accent_color || '#00d4ff'}
                    onChange={(e) => brandingForm.setFieldValue('brand_accent_color', e.target.value)}
                    style={{ width: 36, height: 36, border: '1px solid #1a1f2e', borderRadius: 4, cursor: 'pointer', background: 'transparent', padding: 2 }}
                  />
                  <TextInput
                    size="xs"
                    w={90}
                    {...brandingForm.getInputProps('brand_accent_color')}
                    styles={inputStyles}
                  />
                </Group>
              </div>
            </Group>

            <Button type="submit" color="cyan" loading={brandingSaving} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif" } }}>
              SAVE BRANDING
            </Button>
          </Stack>
        </form>
      </Card>
    </Stack>
  );
}
