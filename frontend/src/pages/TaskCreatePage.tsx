import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { ErrorPanel } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { useCreateTask } from "../features/tasks/queries";

const taskSchema = z.object({
  task: z
    .string()
    .trim()
    .min(1, "Describe the enterprise task to run.")
    .max(10_000, "Task text must not exceed 10,000 characters."),
  output_format: z.enum(["pdf", "json"]),
  require_approval: z.boolean(),
  max_steps: z.number().int().min(1).max(10).optional(),
});

type TaskForm = z.infer<typeof taskSchema>;

export function TaskCreatePage() {
  const navigate = useNavigate();
  const create = useCreateTask();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<TaskForm>({
    resolver: zodResolver(taskSchema),
    defaultValues: {
      task: "",
      output_format: "pdf",
      require_approval: false,
    },
  });

  const submit = handleSubmit(async (values) => {
    const created = await create.mutateAsync({
      task: values.task,
      output_format: values.output_format,
      ...(values.require_approval ? { require_approval: true } : {}),
      ...(values.max_steps === undefined
        ? {}
        : { max_steps: values.max_steps }),
    });
    await navigate(`/tasks/${encodeURIComponent(created.task_id)}`);
  });

  return (
    <>
      <PageHeader
        eyebrow="New governed execution"
        title="Run enterprise task"
        description="The Agent will understand, plan, execute, collect Evidence, generate an Artifact, and verify the result within the approved scope."
      />
      <section className="panel form-panel">
        <form
          onSubmit={(event) => void submit(event).catch(() => undefined)}
          noValidate
        >
          <div className="field">
            <label htmlFor="task">What do you want the Agent to do?</label>
            <textarea
              id="task"
              rows={8}
              placeholder="Analyze supplier quality for Q2 2026, compare with Q1, check the approved quality policy, and generate a PDF report."
              aria-invalid={Boolean(errors.task)}
              aria-describedby={errors.task ? "task-error" : "task-help"}
              {...register("task")}
            />
            {errors.task ? (
              <p className="field-error" id="task-error">
                {errors.task.message}
              </p>
            ) : (
              <p className="field-help" id="task-help">
                Include an explicit year and quarter. Identity and data scope
                come from the trusted server context.
              </p>
            )}
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="output-format">Report format</label>
              <select id="output-format" {...register("output_format")}>
                <option value="pdf">PDF report</option>
                <option value="json">JSON report</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="max-steps">
                Maximum steps <span className="optional">Optional</span>
              </label>
              <input
                id="max-steps"
                type="number"
                min={1}
                max={10}
                aria-invalid={Boolean(errors.max_steps)}
                {...register("max_steps", {
                  setValueAs: (value: string) =>
                    value === "" ? undefined : Number(value),
                })}
              />
              {errors.max_steps && (
                <p className="field-error">Use a value from 1 to 10.</p>
              )}
            </div>
          </div>
          <label className="checkbox-field">
            <input type="checkbox" {...register("require_approval")} />
            <span>
              <strong>Require governed approval</strong>
              <small>
                Tightens execution by pausing before the controlled action.
              </small>
            </span>
          </label>
          {create.isError && (
            <ErrorPanel error={create.error} title="Task was not created" />
          )}
          <div className="form-actions">
            <button
              className="button"
              type="submit"
              disabled={create.isPending}
            >
              {create.isPending ? "Running governed workflow…" : "Run task"}
            </button>
          </div>
        </form>
      </section>
      <aside className="boundary-callout">
        <strong>Governance boundary</strong>
        <p>
          The browser cannot choose a tenant, role, supplier permission,
          database, RAG source, or tool. Those constraints remain server-owned.
        </p>
      </aside>
    </>
  );
}
