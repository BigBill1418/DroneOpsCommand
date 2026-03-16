import { useEffect, useState, useRef, useCallback } from 'react';
import {
  Button,
  Card,
  Group,
  Select,
  Stack,
  Stepper,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
  NumberInput,
  ActionIcon,
  Table,
  Badge,
  Checkbox,
  Loader,
  Progress,
  Image,
  SimpleGrid,
  ScrollArea,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import {
  IconPlus,
  IconTrash,
  IconRobot,
  IconFileText,
  IconSend,
  IconDownload,
  IconUpload,
  IconPhoto,
  IconRefresh,
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Aircraft, Customer, CoverageData, RateTemplate } from '../api/types';
import FlightMap from '../components/FlightMap/FlightMap';
import AircraftCard from '../components/AircraftCard/AircraftCard';
import RichTextEditor from '../components/RichTextEditor/RichTextEditor';

const inputStyles = {
  input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2' },
  label: { color: '#5a6478', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', letterSpacing: '1px' },
};

const missionTypes = [
  { value: 'sar', label: 'Search & Rescue' },
  { value: 'videography', label: 'Videography' },
  { value: 'lost_pet', label: 'Lost Pet Recovery' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'mapping', label: 'Mapping' },
  { value: 'photography', label: 'Photography' },
  { value: 'survey', label: 'Survey' },
  { value: 'security_investigations', label: 'Security & Investigations' },
  { value: 'other', label: 'Other' },
];

const lineItemCategories = [
  { value: 'travel', label: 'Travel' },
  { value: 'billed_time', label: 'Billed Time' },
  { value: 'rapid_deployment', label: 'Rapid Deployment' },
  { value: 'equipment', label: 'Equipment' },
  { value: 'special', label: 'Special Circumstances' },
  { value: 'other', label: 'Other' },
];

export default function MissionNew() {
  const [active, setActive] = useState(0);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);
  const [missionId, setMissionId] = useState<string | null>(null);

  // Flight selection
  const [availableFlights, setAvailableFlights] = useState<any[]>([]);
  const [selectedFlights, setSelectedFlights] = useState<any[]>([]);
  const [flightsLoading, setFlightsLoading] = useState(false);

  // Map
  const [mapGeojson, setMapGeojson] = useState<any>(null);
  const [coverage, setCoverage] = useState<CoverageData | null>(null);

  // Report
  const [narrative, setNarrative] = useState('');
  const [reportContent, setReportContent] = useState('');
  const [generating, setGenerating] = useState(false);

  // Images
  const [uploadedImages, setUploadedImages] = useState<{ name: string; status: 'pending' | 'uploading' | 'done' | 'error' }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Invoice
  const [lineItems, setLineItems] = useState<any[]>([]);
  const [rateTemplates, setRateTemplates] = useState<RateTemplate[]>([]);
  const [paidInFull, setPaidInFull] = useState(false);

  const navigate = useNavigate();

  const form = useForm({
    initialValues: {
      customer_id: '',
      title: '',
      mission_type: 'other',
      description: '',
      mission_date: null as Date | null,
      location_name: '',
      is_billable: false,
    },
  });

  useEffect(() => {
    api.get('/customers').then((r) => setCustomers(r.data)).catch(() => {});
    api.get('/aircraft').then((r) => setAircraft(r.data)).catch(() => {});
    api.get('/rate-templates?active_only=true').then((r) => setRateTemplates(r.data)).catch(() => {});
  }, []);

  // Load flights when step 2 (Flights) becomes active
  useEffect(() => {
    if (active === 1 && missionId && availableFlights.length === 0 && !flightsLoading) {
      loadFlights();
    }
  }, [active, missionId]);

  const loadFlights = async () => {
    setFlightsLoading(true);
    try {
      const resp = await api.get('/flights');
      setAvailableFlights(Array.isArray(resp.data) ? resp.data : []);
    } catch {
      notifications.show({ title: 'OpenDroneLog', message: 'Could not fetch flights. Check the OpenDroneLog URL in Settings and use Test Connection to verify.', color: 'yellow' });
    } finally {
      setFlightsLoading(false);
    }
  };

  const loadMapData = async () => {
    if (!missionId) return;
    try {
      const [mapResp, covResp] = await Promise.all([
        api.get(`/missions/${missionId}/map`),
        api.get(`/missions/${missionId}/map/coverage`),
      ]);
      setMapGeojson(mapResp.data);
      setCoverage(covResp.data);
    } catch {}
  };

  // Step 1: Create mission
  const handleCreateMission = async () => {
    const values = form.values;
    try {
      const resp = await api.post('/missions', {
        ...values,
        customer_id: values.customer_id || null,
        mission_date: values.mission_date?.toISOString().split('T')[0] || null,
      });
      setMissionId(resp.data.id);
      notifications.show({ title: 'Mission Created', message: resp.data.title, color: 'cyan' });
      loadFlights();
      setActive(1);
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to create mission', color: 'red' });
    }
  };

  // Aircraft assigned to this mission
  const [missionAircraft, setMissionAircraft] = useState<string[]>([]);

  // Step 2: Add flights
  const handleAddFlight = async (flight: any, aircraftId?: string) => {
    if (!missionId) return;
    try {
      const resp = await api.post(`/missions/${missionId}/flights`, {
        opendronelog_flight_id: String(flight.id || flight.flight_id),
        aircraft_id: aircraftId || null,
        flight_data_cache: flight,
      });
      setSelectedFlights((prev) => [...prev, { ...flight, _flightId: resp.data.id, _aircraftId: aircraftId || null }]);
      loadMapData();
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to add flight', color: 'red' });
    }
  };

  const handleAssignAircraft = async (flightIndex: number, aircraftId: string | null) => {
    if (!missionId) return;
    const flight = selectedFlights[flightIndex];
    if (!flight?._flightId) return;
    try {
      await api.put(`/missions/${missionId}/flights/${flight._flightId}`, {
        opendronelog_flight_id: String(flight.id || flight.flight_id),
        aircraft_id: aircraftId,
        flight_data_cache: flight,
      });
      setSelectedFlights((prev) => {
        const updated = [...prev];
        updated[flightIndex] = { ...updated[flightIndex], _aircraftId: aircraftId };
        return updated;
      });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to assign aircraft', color: 'red' });
    }
  };

  // Step 3: Upload images
  const collectFilesFromEntry = async (entry: FileSystemEntry): Promise<File[]> => {
    if (entry.isFile) {
      return new Promise((resolve) => {
        (entry as FileSystemFileEntry).file((f) => {
          if (f.type.startsWith('image/')) resolve([f]);
          else resolve([]);
        });
      });
    }
    if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const entries: FileSystemEntry[] = await new Promise((resolve) => reader.readEntries(resolve));
      const nested = await Promise.all(entries.map(collectFilesFromEntry));
      return nested.flat();
    }
    return [];
  };

  const uploadFiles = async (files: File[]) => {
    if (!missionId || files.length === 0) return;
    const imageFiles = files.filter((f) => f.type.startsWith('image/'));
    if (imageFiles.length === 0) {
      notifications.show({ title: 'No images', message: 'No image files found in selection', color: 'yellow' });
      return;
    }

    setUploading(true);
    const tracker = imageFiles.map((f) => ({ name: f.name, status: 'pending' as const }));
    setUploadedImages((prev) => [...prev, ...tracker]);

    let successCount = 0;
    for (let i = 0; i < imageFiles.length; i++) {
      setUploadedImages((prev) => {
        const updated = [...prev];
        const idx = prev.length - imageFiles.length + i;
        updated[idx] = { ...updated[idx], status: 'uploading' };
        return updated;
      });

      const formData = new FormData();
      formData.append('file', imageFiles[i]);
      formData.append('caption', '');
      try {
        await api.post(`/missions/${missionId}/images`, formData, {
          timeout: 120000,  // 2 min timeout for large files
        });
        successCount++;
        setUploadedImages((prev) => {
          const updated = [...prev];
          const idx = prev.length - imageFiles.length + i;
          updated[idx] = { ...updated[idx], status: 'done' };
          return updated;
        });
      } catch {
        setUploadedImages((prev) => {
          const updated = [...prev];
          const idx = prev.length - imageFiles.length + i;
          updated[idx] = { ...updated[idx], status: 'error' };
          return updated;
        });
      }
    }

    setUploading(false);
    notifications.show({
      title: 'Upload Complete',
      message: `${successCount} of ${imageFiles.length} image(s) uploaded successfully`,
      color: successCount === imageFiles.length ? 'cyan' : 'yellow',
    });
  };

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);

    const items = e.dataTransfer.items;
    if (items) {
      const allFiles: File[] = [];
      const entries = Array.from(items)
        .map((item) => item.webkitGetAsEntry?.())
        .filter(Boolean) as FileSystemEntry[];

      if (entries.length > 0) {
        const nested = await Promise.all(entries.map(collectFilesFromEntry));
        allFiles.push(...nested.flat());
      } else {
        // Fallback for browsers without webkitGetAsEntry
        allFiles.push(...Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('image/')));
      }
      await uploadFiles(allFiles);
    }
  }, [missionId]);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files) await uploadFiles(Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // Step 4: Generate report
  const handleGenerate = async () => {
    if (!missionId) return;
    setGenerating(true);
    try {
      const resp = await api.post(`/missions/${missionId}/report/generate`, {
        user_narrative: narrative,
      });
      setReportContent(resp.data.final_content || '');
      notifications.show({ title: 'Report Generated', message: 'LLM report is ready for review', color: 'cyan' });
    } catch (err: any) {
      notifications.show({
        title: 'Generation Failed',
        message: err.response?.data?.detail || 'Could not generate report. Is Ollama running?',
        color: 'red',
      });
    } finally {
      setGenerating(false);
    }
  };

  // Step 5: Invoice
  const addLineItem = () => {
    setLineItems((prev) => [
      ...prev,
      { description: '', category: 'other', quantity: 1, unit_price: 0 },
    ]);
  };

  const handleSaveInvoice = async () => {
    if (!missionId) return;
    try {
      await api.post(`/missions/${missionId}/invoice`, { tax_rate: 0, paid_in_full: paidInFull });
      for (const item of lineItems) {
        if (item.description) {
          await api.post(`/missions/${missionId}/invoice/items`, item);
        }
      }
      notifications.show({ title: 'Invoice Saved', message: 'Line items added', color: 'cyan' });
    } catch {}
  };

  // Step 6: Generate PDF & Send
  const handleGeneratePDF = async () => {
    if (!missionId) return;
    try {
      // Save final report content
      if (reportContent) {
        await api.put(`/missions/${missionId}/report`, { final_content: reportContent });
      }
      // Generate PDF
      const resp = await api.post(`/missions/${missionId}/report/pdf`, {}, { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([resp.data], { type: 'application/pdf' }));
      window.open(url, '_blank');
      notifications.show({ title: 'PDF Generated', message: 'Opening PDF preview', color: 'cyan' });
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to generate PDF', color: 'red' });
    }
  };

  const handleSendEmail = async () => {
    if (!missionId) return;
    try {
      await api.post(`/missions/${missionId}/report/send`);
      notifications.show({ title: 'Sent', message: 'Report emailed to customer', color: 'green' });
    } catch (err: any) {
      notifications.show({
        title: 'Send Failed',
        message: err.response?.data?.detail || 'Failed to send email',
        color: 'red',
      });
    }
  };

  const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };

  return (
    <Stack gap="lg">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>NEW MISSION</Title>

      <Stepper
        active={active}
        onStepClick={setActive}
        color="cyan"
        styles={{
          step: { color: '#e8edf2' },
          stepLabel: { color: '#e8edf2', fontFamily: "'Rajdhani', sans-serif" },
          stepDescription: { color: '#5a6478' },
          separator: { borderColor: '#1a1f2e' },
        }}
      >
        {/* Step 1: Mission Details */}
        <Stepper.Step label="Details" description="Mission info">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              <Select
                label="Customer"
                placeholder="Select customer"
                data={customers.map((c) => ({ value: c.id, label: `${c.name}${c.company ? ` (${c.company})` : ''}` }))}
                searchable
                clearable
                {...form.getInputProps('customer_id')}
                styles={inputStyles}
              />
              <TextInput label="Mission Title" required {...form.getInputProps('title')} styles={inputStyles} />
              <Group grow>
                <Select label="Mission Type" data={missionTypes} {...form.getInputProps('mission_type')} styles={inputStyles} />
                <DateInput label="Date" {...form.getInputProps('mission_date')} styles={inputStyles} />
              </Group>
              <TextInput label="Location" {...form.getInputProps('location_name')} styles={inputStyles} />
              <Textarea label="Description" {...form.getInputProps('description')} styles={inputStyles} minRows={3} />
              <Switch
                label="Billable Mission"
                color="cyan"
                checked={form.values.is_billable}
                onChange={(e) => form.setFieldValue('is_billable', e.currentTarget.checked)}
              />
              <Button color="cyan" onClick={handleCreateMission} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                CREATE & CONTINUE
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* Step 2: Flights & Aircraft */}
        <Stepper.Step label="Flights" description="Flights & aircraft">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="sm">
              {/* Aircraft selection — compact inline */}
              <Group justify="space-between" align="center">
                <Text c="#e8edf2" fw={600} style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                  AIRCRAFT USED
                </Text>
                <Checkbox.Group value={missionAircraft} onChange={setMissionAircraft}>
                  <Group gap="xs">
                    {aircraft.map((a) => (
                      <Checkbox
                        key={a.id}
                        value={a.id}
                        label={a.model_name}
                        color="cyan"
                        size="xs"
                        styles={{ label: { color: '#e8edf2', fontSize: '12px' } }}
                      />
                    ))}
                  </Group>
                </Checkbox.Group>
              </Group>

              {/* Flight logs — scrollable table */}
              <Group justify="space-between" align="center">
                <Group gap="xs">
                  <Text c="#e8edf2" fw={600} style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                    FLIGHT LOGS
                  </Text>
                  <ActionIcon
                    variant="subtle"
                    color="cyan"
                    size="sm"
                    onClick={loadFlights}
                    loading={flightsLoading}
                    title="Reload flights from OpenDroneLog"
                  >
                    <IconRefresh size={14} />
                  </ActionIcon>
                </Group>
                {selectedFlights.length > 0 && (
                  <Badge color="cyan" variant="light" size="sm">{selectedFlights.length} selected</Badge>
                )}
              </Group>

              {flightsLoading ? (
                <Group justify="center" py="md"><Loader color="cyan" /></Group>
              ) : availableFlights.length === 0 ? (
                <Text c="#5a6478" size="sm">No flights found. Check OpenDroneLog URL in Settings.</Text>
              ) : (
                <ScrollArea h={280} type="auto" offsetScrollbars styles={{ viewport: { borderRadius: 4 } }}>
                  <Table verticalSpacing={4} styles={{
                    table: { color: '#e8edf2', fontSize: '12px' },
                    th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '10px', borderBottom: '1px solid #1a1f2e', padding: '6px 8px', position: 'sticky', top: 0, background: '#0e1117', zIndex: 1 },
                    td: { borderBottom: '1px solid #1a1f2e', padding: '4px 8px' },
                  }}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th w={40}></Table.Th>
                        <Table.Th>DATE</Table.Th>
                        <Table.Th>DRONE</Table.Th>
                        <Table.Th>DURATION</Table.Th>
                        <Table.Th>AIRCRAFT</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {availableFlights.map((flight: any, i: number) => {
                        const selectedIdx = selectedFlights.findIndex((f) => (f.id || f.flight_id) === (flight.id || flight.flight_id));
                        const isSelected = selectedIdx >= 0;
                        return (
                          <Table.Tr key={i} style={{ background: isSelected ? 'rgba(0,212,255,0.05)' : undefined }}>
                            <Table.Td>
                              <Checkbox
                                color="cyan"
                                size="xs"
                                checked={isSelected}
                                onChange={() => !isSelected && handleAddFlight(flight, missionAircraft[0] || undefined)}
                                disabled={isSelected}
                              />
                            </Table.Td>
                            <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '11px' }}>{flight.date || flight.start_time || '—'}</Table.Td>
                            <Table.Td>{flight.drone || flight.aircraft || '—'}</Table.Td>
                            <Table.Td>{flight.duration || flight.flight_time || '—'}</Table.Td>
                            <Table.Td>
                              {isSelected ? (
                                <Select
                                  size="xs"
                                  placeholder="Assign..."
                                  data={aircraft.map((a) => ({ value: a.id, label: a.model_name }))}
                                  value={selectedFlights[selectedIdx]?._aircraftId || null}
                                  onChange={(val) => handleAssignAircraft(selectedIdx, val)}
                                  clearable
                                  styles={{ input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', minWidth: 130, height: 28, minHeight: 28, fontSize: '11px' } }}
                                />
                              ) : (
                                <Text c="#5a6478" size="xs">—</Text>
                              )}
                            </Table.Td>
                          </Table.Tr>
                        );
                      })}
                    </Table.Tbody>
                  </Table>
                </ScrollArea>
              )}

              {/* Map — only if flights selected */}
              {selectedFlights.length > 0 && mapGeojson && (
                <FlightMap geojson={mapGeojson} coverage={coverage ?? undefined} height="250px" />
              )}

              <Button color="cyan" onClick={() => setActive(2)} fullWidth styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                CONTINUE
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* Step 3: Images */}
        <Stepper.Step label="Images" description="Upload photos">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              <Text c="#e8edf2" fw={600}>Upload Mission Images</Text>
              <Text c="#5a6478" size="xs">Drag and drop images or folders here. Large images are automatically resized for the report.</Text>

              {/* Drop zone */}
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  border: `2px dashed ${dragOver ? '#00d4ff' : '#1a1f2e'}`,
                  borderRadius: 8,
                  padding: '40px 20px',
                  textAlign: 'center',
                  cursor: 'pointer',
                  background: dragOver ? 'rgba(0, 212, 255, 0.05)' : '#050608',
                  transition: 'all 0.2s',
                }}
              >
                <IconUpload size={36} color={dragOver ? '#00d4ff' : '#5a6478'} style={{ marginBottom: 8 }} />
                <Text c={dragOver ? '#00d4ff' : '#5a6478'} fw={600}>
                  {uploading ? 'Uploading...' : 'Drop images or folders here, or click to browse'}
                </Text>
                <Text c="#5a6478" size="xs" mt={4}>Supports JPG, PNG, HEIC, and other image formats</Text>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                onChange={handleFileSelect}
                style={{ display: 'none' }}
              />

              {/* Upload progress */}
              {uploadedImages.length > 0 && (
                <Stack gap="xs">
                  <Text c="#00d4ff" fw={600} size="sm" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                    UPLOADED ({uploadedImages.filter((i) => i.status === 'done').length}/{uploadedImages.length})
                  </Text>
                  {uploadedImages.map((img, i) => (
                    <Group key={i} gap="xs">
                      <IconPhoto size={14} color={img.status === 'done' ? '#2ecc40' : img.status === 'error' ? '#ff6b6b' : '#5a6478'} />
                      <Text size="xs" c={img.status === 'error' ? '#ff6b6b' : '#e8edf2'} style={{ flex: 1, fontFamily: "'Share Tech Mono', monospace" }}>
                        {img.name}
                      </Text>
                      {img.status === 'uploading' && <Loader size={12} color="cyan" />}
                      {img.status === 'done' && <Badge size="xs" color="green">Done</Badge>}
                      {img.status === 'error' && <Badge size="xs" color="red">Failed</Badge>}
                    </Group>
                  ))}
                </Stack>
              )}

              <Button color="cyan" onClick={() => setActive(3)} disabled={uploading} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                CONTINUE
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* Step 4: Report */}
        <Stepper.Step label="Report" description="Generate with AI">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              <Text c="#e8edf2" fw={600}>Operator Notes</Text>
              <Textarea
                placeholder="Describe what happened during the mission, conditions, findings, outcome..."
                value={narrative}
                onChange={(e) => setNarrative(e.target.value)}
                minRows={5}
                styles={inputStyles}
              />
              <Button
                leftSection={generating ? <Loader size={16} color="white" /> : <IconRobot size={16} />}
                color="cyan"
                onClick={handleGenerate}
                disabled={generating || !narrative}
                styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
              >
                {generating ? 'GENERATING...' : 'GENERATE REPORT'}
              </Button>

              {reportContent && (
                <>
                  <Text c="#00d4ff" fw={600} style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                    GENERATED REPORT
                  </Text>
                  <RichTextEditor
                    content={reportContent}
                    onChange={setReportContent}
                    minHeight="400px"
                  />
                </>
              )}

              <Button color="cyan" onClick={() => setActive(4)} disabled={!reportContent} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                CONTINUE
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* Step 5: Invoice */}
        <Stepper.Step label="Invoice" description="Billing details">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              {!form.values.is_billable ? (
                <Text c="#5a6478">This mission is not billable. You can skip this step.</Text>
              ) : (
                <>
                  <Group justify="space-between">
                    <Text c="#e8edf2" fw={600}>Line Items</Text>
                    <Group gap="xs">
                      <Select
                        placeholder="Add from template..."
                        data={rateTemplates.map((t) => ({
                          value: t.id,
                          label: `${t.name} ($${t.default_rate}/${t.default_unit || 'ea'})`,
                        }))}
                        clearable
                        size="xs"
                        onChange={(val) => {
                          if (!val) return;
                          const tmpl = rateTemplates.find((t) => t.id === val);
                          if (tmpl) {
                            setLineItems((prev) => [
                              ...prev,
                              {
                                description: tmpl.name + (tmpl.description ? ` — ${tmpl.description}` : ''),
                                category: tmpl.category,
                                quantity: tmpl.default_quantity,
                                unit_price: tmpl.default_rate,
                              },
                            ]);
                          }
                        }}
                        styles={{
                          input: { background: '#050608', borderColor: '#1a1f2e', color: '#e8edf2', width: 280 },
                        }}
                      />
                      <Button
                        leftSection={<IconPlus size={14} />}
                        size="xs"
                        color="cyan"
                        variant="light"
                        onClick={addLineItem}
                      >
                        Blank Item
                      </Button>
                    </Group>
                  </Group>

                  {lineItems.map((item, i) => (
                    <Group key={i} align="end">
                      <TextInput
                        label="Description"
                        style={{ flex: 2 }}
                        value={item.description}
                        onChange={(e) => {
                          const updated = [...lineItems];
                          updated[i].description = e.target.value;
                          setLineItems(updated);
                        }}
                        styles={inputStyles}
                      />
                      <Select
                        label="Category"
                        data={lineItemCategories}
                        value={item.category}
                        onChange={(val) => {
                          const updated = [...lineItems];
                          updated[i].category = val || 'other';
                          setLineItems(updated);
                        }}
                        styles={inputStyles}
                        style={{ flex: 1 }}
                      />
                      <NumberInput
                        label="Qty"
                        value={item.quantity}
                        onChange={(val) => {
                          const updated = [...lineItems];
                          updated[i].quantity = val || 1;
                          setLineItems(updated);
                        }}
                        min={0}
                        styles={inputStyles}
                        style={{ width: 80 }}
                      />
                      <NumberInput
                        label="Price"
                        value={item.unit_price}
                        onChange={(val) => {
                          const updated = [...lineItems];
                          updated[i].unit_price = val || 0;
                          setLineItems(updated);
                        }}
                        min={0}
                        decimalScale={2}
                        prefix="$"
                        styles={inputStyles}
                        style={{ width: 110 }}
                      />
                      <ActionIcon color="red" variant="subtle" onClick={() => setLineItems(lineItems.filter((_, j) => j !== i))}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  ))}

                  {lineItems.length > 0 && (
                    <Text c="#00d4ff" ta="right" fw={700} style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px' }}>
                      TOTAL: ${lineItems.reduce((sum, item) => sum + (item.quantity || 0) * (item.unit_price || 0), 0).toFixed(2)}
                    </Text>
                  )}

                  <Switch
                    label="Paid in Full"
                    color="green"
                    checked={paidInFull}
                    onChange={(e) => setPaidInFull(e.currentTarget.checked)}
                    styles={{ label: { color: '#e8edf2', fontFamily: "'Share Tech Mono', monospace" } }}
                  />

                  <Button color="cyan" onClick={handleSaveInvoice} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                    SAVE INVOICE
                  </Button>
                </>
              )}

              <Button color="cyan" onClick={() => setActive(5)} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
                CONTINUE TO REVIEW
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* Step 6: Review & Send */}
        <Stepper.Step label="Send" description="PDF & email">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              <Text c="#e8edf2" fw={600} size="lg" style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                REVIEW & SEND
              </Text>

              {/* Aircraft used */}
              {missionAircraft.length > 0 && (
                <Group>
                  {aircraft.filter((a) => missionAircraft.includes(a.id)).map((a) => (
                    <AircraftCard key={a.id} aircraft={a} compact />
                  ))}
                </Group>
              )}

              {/* Map preview */}
              {mapGeojson && <FlightMap geojson={mapGeojson} coverage={coverage ?? undefined} height="300px" />}

              <Group>
                <Button
                  leftSection={<IconDownload size={16} />}
                  color="cyan"
                  onClick={handleGeneratePDF}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  GENERATE PDF
                </Button>
                <Button
                  leftSection={<IconSend size={16} />}
                  color="orange"
                  variant="light"
                  onClick={handleSendEmail}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}
                >
                  EMAIL TO CUSTOMER
                </Button>
              </Group>

              <Button
                variant="subtle"
                color="gray"
                onClick={() => navigate(`/missions/${missionId}`)}
              >
                Go to Mission Detail →
              </Button>
            </Stack>
          </Card>
        </Stepper.Step>
      </Stepper>
    </Stack>
  );
}
