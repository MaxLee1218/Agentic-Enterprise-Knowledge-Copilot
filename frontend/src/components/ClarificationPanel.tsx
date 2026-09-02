import { useMemo, useState, type FormEvent } from "react";

import type { ClarificationSubmissionRequest, Task } from "../api/types";
import { useSubmitClarification } from "../features/tasks/queries";
import { ErrorPanel } from "./PageState";

type PendingClarification = NonNullable<Task["pending_clarification"]>;
type Question = PendingClarification["questions"][number];
type Answers = NonNullable<ClarificationSubmissionRequest["answers"]>;

interface ClarificationPanelProps {
  taskId: string;
  clarification: PendingClarification;
}

interface DateRangeValue {
  start_date?: string;
  end_date?: string;
}

function dateRangeValue(value: unknown): DateRangeValue {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const record = value as Record<string, unknown>;
  return {
    start_date:
      typeof record.start_date === "string" ? record.start_date : undefined,
    end_date: typeof record.end_date === "string" ? record.end_date : undefined,
  };
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function selectedValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function hasCompleteAnswer(question: Question, value: unknown) {
  if (question.input_type === "date_range") {
    const range = dateRangeValue(value);
    return Boolean(range.start_date && range.end_date);
  }
  if (question.input_type === "multi_select") {
    return Array.isArray(value) && value.length > 0;
  }
  return typeof value === "string" && Boolean(value.trim());
}

export function ClarificationPanel({
  taskId,
  clarification,
}: ClarificationPanelProps) {
  const [answers, setAnswers] = useState<Answers>({});
  const [message, setMessage] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const submit = useSubmitClarification(taskId, clarification.clarification_id);

  const partialDateRange = useMemo(
    () =>
      clarification.questions.some((question) => {
        if (question.input_type !== "date_range") return false;
        const range = dateRangeValue(answers[question.field]);
        return Boolean(range.start_date) !== Boolean(range.end_date);
      }),
    [answers, clarification.questions],
  );

  function setAnswer(field: string, value: unknown) {
    setAnswers((current) => {
      const next = { ...current };
      if (value === undefined || value === "") delete next[field];
      else next[field] = value;
      return next;
    });
    setLocalError(null);
  }

  function setDateRange(
    field: string,
    key: keyof DateRangeValue,
    value: string,
  ) {
    const current = dateRangeValue(answers[field]);
    const next = { ...current, [key]: value };
    setAnswer(field, next.start_date || next.end_date ? next : undefined);
  }

  function toggleMultiValue(field: string, option: string, checked: boolean) {
    const current = answers[field];
    const values = selectedValues(current);
    const next = checked
      ? [...new Set([...values, option])]
      : values.filter((value) => value !== option);
    setAnswer(field, next.length ? next : undefined);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const completedAnswers = Object.fromEntries(
      clarification.questions
        .filter((question) =>
          hasCompleteAnswer(question, answers[question.field]),
        )
        .map((question) => [question.field, answers[question.field]]),
    ) as Answers;
    if (partialDateRange) {
      setLocalError(
        "Provide both a start and end date, or leave the date range blank.",
      );
      return;
    }
    if (Object.keys(completedAnswers).length === 0 && !message.trim()) {
      setLocalError("Answer at least one question or add a message.");
      return;
    }
    setLocalError(null);
    submit.mutate({
      answers: completedAnswers,
      message: message.trim() || null,
    });
  }

  return (
    <section
      className="panel clarification-panel"
      aria-labelledby="clarification-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            Information needed · Round {clarification.round}
          </p>
          <h2 id="clarification-title">
            Agent needs more information before planning
          </h2>
        </div>
      </div>
      <p className="clarification-intro">
        Answer any information you can provide now. Partial answers are
        accepted; the same task will resume validation and ask only for what
        remains.
      </p>
      <form onSubmit={handleSubmit} noValidate>
        {clarification.questions.map((question) => (
          <div className="clarification-question" key={question.field}>
            <label htmlFor={`clarification-${question.field}`}>
              {question.prompt}
              {!question.required && (
                <span className="optional"> Optional</span>
              )}
            </label>
            <p className="field-help">{question.reason}</p>
            {question.input_type === "date_range" && (
              <div className="form-grid">
                <label>
                  Start date
                  <input
                    id={`clarification-${question.field}`}
                    type="date"
                    value={
                      dateRangeValue(answers[question.field]).start_date ?? ""
                    }
                    onChange={(event) =>
                      setDateRange(
                        question.field,
                        "start_date",
                        event.target.value,
                      )
                    }
                  />
                </label>
                <label>
                  End date
                  <input
                    type="date"
                    value={
                      dateRangeValue(answers[question.field]).end_date ?? ""
                    }
                    onChange={(event) =>
                      setDateRange(
                        question.field,
                        "end_date",
                        event.target.value,
                      )
                    }
                  />
                </label>
              </div>
            )}
            {question.input_type === "date" && (
              <input
                id={`clarification-${question.field}`}
                type="date"
                value={stringValue(answers[question.field])}
                onChange={(event) =>
                  setAnswer(question.field, event.target.value)
                }
              />
            )}
            {question.input_type === "single_select" && (
              <select
                id={`clarification-${question.field}`}
                value={stringValue(answers[question.field])}
                onChange={(event) =>
                  setAnswer(question.field, event.target.value)
                }
              >
                <option value="">Choose an authorized value</option>
                {question.allowed_values.map((value) => (
                  <option value={value} key={value}>
                    {value}
                  </option>
                ))}
              </select>
            )}
            {question.input_type === "multi_select" && (
              <fieldset
                id={`clarification-${question.field}`}
                className="choice-group"
              >
                <legend className="sr-only">{question.prompt}</legend>
                {question.allowed_values.map((value) => (
                  <label className="checkbox-field" key={value}>
                    <input
                      type="checkbox"
                      checked={selectedValues(answers[question.field]).includes(
                        value,
                      )}
                      onChange={(event) =>
                        toggleMultiValue(
                          question.field,
                          value,
                          event.target.checked,
                        )
                      }
                    />
                    <span>{value}</span>
                  </label>
                ))}
              </fieldset>
            )}
            {question.input_type === "text" && (
              <input
                id={`clarification-${question.field}`}
                type="text"
                value={stringValue(answers[question.field])}
                onChange={(event) =>
                  setAnswer(question.field, event.target.value)
                }
              />
            )}
          </div>
        ))}
        <div className="field">
          <label htmlFor="clarification-message">
            Answer in your own words <span className="optional">Optional</span>
          </label>
          <textarea
            id="clarification-message"
            rows={3}
            maxLength={4000}
            value={message}
            onChange={(event) => {
              setMessage(event.target.value);
              setLocalError(null);
            }}
            placeholder="You can provide one or more answers in natural language."
          />
        </div>
        {localError && (
          <p className="field-error" role="alert">
            {localError}
          </p>
        )}
        {submit.isError && (
          <ErrorPanel
            error={submit.error}
            title="Clarification response was not accepted"
          />
        )}
        <div className="form-actions">
          <button className="button" type="submit" disabled={submit.isPending}>
            {submit.isPending ? "Submitting…" : "Submit information and resume"}
          </button>
        </div>
      </form>
    </section>
  );
}
