import type { ReactNode } from "react";

export interface MetadataItem {
  label: string;
  value: ReactNode;
}

export function MetadataList({
  items,
  compact = false,
}: {
  items: MetadataItem[];
  compact?: boolean;
}) {
  return (
    <dl className={`metadata-list ${compact ? "metadata-list--compact" : ""}`}>
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
