import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

const MAX_TASK_LENGTH = 10_000;

export function TaskComposer({
  value,
  onChange,
  onSubmit,
  pending,
  disabled,
  placeholder,
  helperText,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  pending?: boolean;
  disabled?: boolean;
  placeholder: string;
  helperText?: string;
  error?: string | null;
}) {
  const composing = useRef(false);
  const [lengthError, setLengthError] = useState<string | null>(null);
  const canSend =
    value.trim().length > 0 && !pending && !disabled && !lengthError;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || pending || disabled) return;
    if (trimmed.length > MAX_TASK_LENGTH) {
      setLengthError(
        `Message must not exceed ${MAX_TASK_LENGTH.toLocaleString()} characters.`,
      );
      return;
    }
    void onSubmit();
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      !composing.current
    ) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form className="task-composer" onSubmit={submit}>
      <div
        className={`task-composer__box ${disabled ? "task-composer__box--disabled" : ""}`}
      >
        <label className="sr-only" htmlFor="task-composer-input">
          Message the Enterprise Knowledge Copilot
        </label>
        <textarea
          id="task-composer-input"
          value={value}
          rows={1}
          maxLength={MAX_TASK_LENGTH + 1}
          disabled={disabled || pending}
          placeholder={placeholder}
          aria-describedby="composer-help composer-error"
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onKeyDown={keyDown}
          onChange={(event) => {
            onChange(event.target.value);
            setLengthError(
              event.target.value.length > MAX_TASK_LENGTH
                ? `Message must not exceed ${MAX_TASK_LENGTH.toLocaleString()} characters.`
                : null,
            );
          }}
        />
        <button
          className="composer-send"
          type="submit"
          disabled={!canSend}
          aria-label="Send message"
        >
          <span aria-hidden="true">↑</span>
        </button>
      </div>
      <div className="task-composer__meta">
        <p id="composer-help">
          {helperText ?? "Enter to send · Shift+Enter for a new line"}
        </p>
        <p id="composer-error" className="composer-error" role="alert">
          {lengthError ?? error}
        </p>
      </div>
    </form>
  );
}
