export function JsonPreview({
  value,
  label,
}: {
  value: unknown;
  label: string;
}) {
  return (
    <div className="json-preview">
      <div className="json-preview__label">{label}</div>
      <pre>
        <code>{JSON.stringify(value, null, 2)}</code>
      </pre>
    </div>
  );
}
