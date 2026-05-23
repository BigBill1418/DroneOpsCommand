import {
  ActionIcon,
  Button,
  Card,
  Divider,
  Group,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { IconTrash } from '@tabler/icons-react';
import { cardStyle, inputStyles, monoFont } from '../shared/styles';

export interface EditableLineItem {
  description: string;
  category: string;
  quantity: number;
  unit_price: number;
}

interface LineItemFieldsProps {
  item: EditableLineItem;
  index: number;
  isMobile: boolean;
  categories: { value: string; label: string }[];
  onChange: (patch: Partial<EditableLineItem>) => void;
  onRemove: () => void;
}

/**
 * One editable invoice line item, rendered responsively.
 *
 * Desktop: a single dense row (Description / Category / Qty / Price /
 * line-total / delete) — the long-standing layout.
 *
 * Mobile: a self-contained card with full-width stacked fields and a
 * visible per-item line total, so each item is legible and editable
 * without horizontal scrolling on a phone in the field.
 */
export function LineItemFields({
  item,
  index,
  isMobile,
  categories,
  onChange,
  onRemove,
}: LineItemFieldsProps) {
  const lineTotal = (item.quantity || 0) * (item.unit_price || 0);

  if (!isMobile) {
    return (
      <Group align="end" wrap="nowrap">
        <TextInput
          label="Description"
          style={{ flex: 2 }}
          value={item.description}
          onChange={(e) => onChange({ description: e.target.value })}
          styles={inputStyles}
        />
        <Select
          label="Category"
          data={categories}
          value={item.category}
          onChange={(val) => onChange({ category: val || 'other' })}
          styles={inputStyles}
          style={{ flex: 1 }}
        />
        <NumberInput
          label="Qty"
          value={item.quantity}
          min={0}
          decimalScale={2}
          onChange={(val) => onChange({ quantity: Number.isFinite(Number(val)) ? Number(val) : 0 })}
          styles={inputStyles}
          style={{ width: 90 }}
        />
        <NumberInput
          label="Price"
          value={item.unit_price}
          min={0}
          decimalScale={2}
          prefix="$"
          onChange={(val) => onChange({ unit_price: typeof val === 'number' ? val : 0 })}
          styles={inputStyles}
          style={{ width: 110 }}
        />
        <Text w={92} ta="right" fw={600} c="cyan" style={monoFont}>
          ${lineTotal.toFixed(2)}
        </Text>
        <ActionIcon color="red" variant="subtle" onClick={onRemove} aria-label="Remove line item">
          <IconTrash size={16} />
        </ActionIcon>
      </Group>
    );
  }

  return (
    <Card padding="md" radius="md" style={{ ...cardStyle, background: '#0a0d14' }}>
      <Stack gap="sm">
        <Group justify="space-between" align="center">
          <Text size="xs" c="#5a6478" style={monoFont}>
            ITEM {index + 1}
          </Text>
          <Button
            size="compact-sm"
            color="red"
            variant="subtle"
            leftSection={<IconTrash size={14} />}
            onClick={onRemove}
          >
            Remove
          </Button>
        </Group>
        <TextInput
          label="Description"
          size="md"
          value={item.description}
          onChange={(e) => onChange({ description: e.target.value })}
          styles={inputStyles}
        />
        <Select
          label="Category"
          size="md"
          data={categories}
          value={item.category}
          onChange={(val) => onChange({ category: val || 'other' })}
          styles={inputStyles}
        />
        <Group grow align="end" wrap="nowrap">
          <NumberInput
            label="Qty"
            size="md"
            value={item.quantity}
            min={0}
            decimalScale={2}
            onChange={(val) => onChange({ quantity: Number.isFinite(Number(val)) ? Number(val) : 0 })}
            styles={inputStyles}
          />
          <NumberInput
            label="Price"
            size="md"
            value={item.unit_price}
            min={0}
            decimalScale={2}
            prefix="$"
            onChange={(val) => onChange({ unit_price: typeof val === 'number' ? val : 0 })}
            styles={inputStyles}
          />
        </Group>
        <Divider color="#1a1f2e" />
        <Group justify="space-between" align="center">
          <Text size="sm" c="#5a6478" style={monoFont}>
            Line total
          </Text>
          <Text fw={700} c="cyan" style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: '20px' }}>
            ${lineTotal.toFixed(2)}
          </Text>
        </Group>
      </Stack>
    </Card>
  );
}
