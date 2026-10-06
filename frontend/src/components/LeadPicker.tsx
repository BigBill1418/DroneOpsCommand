/**
 * LeadPicker (ADR-0050) — "Start from a lead" for the new-mission modal and the
 * new-customer form. Renders nothing unless GET /api/leads/status says enabled.
 * Open website leads by default; "Show closed" includes won/lost ones.
 */
import { useEffect, useState } from 'react';
import { Alert, Group, Select, Switch, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';

import { fetchLeadsEnabled, getLead, listLeads, type DocLead, type LeadDetail } from '../api/leads';

interface Props {
  onPick: (detail: LeadDetail) => void;
  /** Picker cleared — the caller must unlink the lead it applied. */
  onClear?: () => void;
}

function ago(iso: string | null): string {
  if (!iso) return '';
  const d = Math.floor((Date.now() - Date.parse(iso)) / 86400000);
  return Number.isFinite(d) ? (d <= 0 ? 'today' : `${d} d ago`) : '';
}

export function leadLabel(l: DocLead): string {
  return [l.name, l.organization, l.service, ago(l.created_at)].filter(Boolean).join(' — ');
}

export default function LeadPicker({ onPick, onClear }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [leads, setLeads] = useState<DocLead[]>([]);
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 300);
  const [includeClosed, setIncludeClosed] = useState(false);
  const [error, setError] = useState(false);
  const [value, setValue] = useState<string | null>(null);

  useEffect(() => {
    fetchLeadsEnabled().then(setEnabled);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    // Ignore responses from superseded searches (LD-2 M-6).
    let current = true;
    listLeads(debounced, includeClosed)
      .then((l) => { if (current) { setLeads(l); setError(false); } })
      .catch(() => { if (current) { setLeads([]); setError(true); } });
    return () => { current = false; };
  }, [enabled, debounced, includeClosed]);

  if (!enabled) return null;

  const pick = async (key: string | null) => {
    setValue(key);
    if (!key) {
      onClear?.();
      return;
    }
    try {
      onPick(await getLead(key));
    } catch {
      setError(true);
    }
  };

  return (
    <div data-testid="lead-picker">
      {error && (
        <Alert color="yellow" variant="light" mb="xs">
          Leads unavailable — fill the form in by hand.
        </Alert>
      )}
      <Select
        label="Start from a lead (optional)"
        placeholder="Search website leads"
        data={leads.map((l) => ({ value: l.key, label: leadLabel(l) }))}
        searchable
        clearable
        clearButtonProps={{ 'aria-label': 'Clear lead', 'aria-hidden': false }}
        searchValue={search}
        onSearchChange={setSearch}
        filter={({ options }) => options}
        value={value}
        onChange={pick}
        nothingFoundMessage={error ? 'Leads unavailable' : 'No matching leads'}
      />
      <Group justify="flex-end" mt={4}>
        <Switch
          size="xs"
          label={<Text size="xs">Show closed leads</Text>}
          checked={includeClosed}
          onChange={(e) => setIncludeClosed(e.currentTarget.checked)}
        />
      </Group>
    </div>
  );
}
