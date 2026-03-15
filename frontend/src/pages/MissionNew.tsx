import { useEffect, useState } from 'react';
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
  FileInput,
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
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { Aircraft, Customer, CoverageData } from '../api/types';
import FlightMap from '../components/FlightMap/FlightMap';
import AircraftCard from '../components/AircraftCard/AircraftCard';

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

  // Invoice
  const [lineItems, setLineItems] = useState<any[]>([]);

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
  }, []);

  const loadFlights = async () => {
    setFlightsLoading(true);
    try {
      const resp = await api.get('/flights');
      setAvailableFlights(Array.isArray(resp.data) ? resp.data : []);
    } catch {
      notifications.show({ title: 'Note', message: 'Could not connect to OpenDroneLog. You can still create the mission manually.', color: 'yellow' });
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

  // Step 2: Add flights
  const handleAddFlight = async (flight: any) => {
    if (!missionId) return;
    try {
      await api.post(`/missions/${missionId}/flights`, {
        opendronelog_flight_id: String(flight.id || flight.flight_id),
        aircraft_id: null,
        flight_data_cache: flight,
      });
      setSelectedFlights((prev) => [...prev, flight]);
      loadMapData();
    } catch {
      notifications.show({ title: 'Error', message: 'Failed to add flight', color: 'red' });
    }
  };

  // Step 3: Upload images
  const handleImageUpload = async (files: File[]) => {
    if (!missionId) return;
    for (const file of files) {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('caption', '');
      try {
        await api.post(`/missions/${missionId}/images`, formData);
      } catch {}
    }
    notifications.show({ title: 'Uploaded', message: `${files.length} image(s) uploaded`, color: 'cyan' });
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
      await api.post(`/missions/${missionId}/invoice`, { tax_rate: 0 });
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

        {/* Step 2: Select Flights */}
        <Stepper.Step label="Flights" description="Select flight logs">
          <Card padding="lg" radius="md" mt="md" style={cardStyle}>
            <Stack gap="md">
              <Text c="#e8edf2" fw={600}>Select flights from OpenDroneLog</Text>
              {flightsLoading ? (
                <Group justify="center"><Loader color="cyan" /></Group>
              ) : availableFlights.length === 0 ? (
                <Text c="#5a6478">No flights found. Ensure OpenDroneLog is configured in Settings.</Text>
              ) : (
                <Table styles={{ table: { color: '#e8edf2' }, th: { color: '#00d4ff', fontFamily: "'Share Tech Mono', monospace", fontSize: '11px', borderBottom: '1px solid #1a1f2e' }, td: { borderBottom: '1px solid #1a1f2e' } }}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>SELECT</Table.Th>
                      <Table.Th>DATE</Table.Th>
                      <Table.Th>DRONE</Table.Th>
                      <Table.Th>DURATION</Table.Th>
                      <Table.Th>DISTANCE</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {availableFlights.map((flight: any, i: number) => {
                      const isSelected = selectedFlights.some((f) => (f.id || f.flight_id) === (flight.id || flight.flight_id));
                      return (
                        <Table.Tr key={i}>
                          <Table.Td>
                            <Checkbox
                              color="cyan"
                              checked={isSelected}
                              onChange={() => !isSelected && handleAddFlight(flight)}
                              disabled={isSelected}
                            />
                          </Table.Td>
                          <Table.Td style={{ fontFamily: "'Share Tech Mono', monospace" }}>{flight.date || flight.start_time || '—'}</Table.Td>
                          <Table.Td>{flight.drone || flight.aircraft || '—'}</Table.Td>
                          <Table.Td>{flight.duration || flight.flight_time || '—'}</Table.Td>
                          <Table.Td>{flight.distance || flight.total_distance || '—'}</Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              )}

              {selectedFlights.length > 0 && (
                <>
                  <Text c="#00d4ff" fw={600} style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' }}>
                    FLIGHT PATH MAP
                  </Text>
                  <FlightMap geojson={mapGeojson} coverage={coverage ?? undefined} height="400px" />
                </>
              )}

              <Button color="cyan" onClick={() => setActive(2)} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
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
              <FileInput
                label="Select images"
                multiple
                accept="image/*"
                onChange={(files) => files && handleImageUpload(files)}
                styles={inputStyles}
              />
              <Button color="cyan" onClick={() => setActive(3)} styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '1px' } }}>
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
                  <Textarea
                    value={reportContent}
                    onChange={(e) => setReportContent(e.target.value)}
                    minRows={15}
                    autosize
                    styles={inputStyles}
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
                    <Button
                      leftSection={<IconPlus size={14} />}
                      size="xs"
                      color="cyan"
                      variant="light"
                      onClick={addLineItem}
                    >
                      Add Item
                    </Button>
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
              {selectedFlights.length > 0 && (
                <Group>
                  {aircraft.slice(0, 3).map((a) => (
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
