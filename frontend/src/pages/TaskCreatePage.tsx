import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { ErrorPanel } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { useCreateTask } from "../features/tasks/queries";

const taskSchema = z.object({
  task_type: z.enum([
    "supplier_quality_analysis.v1",
    "accounts_payable_analysis.v1",
  ]),
  task: z
    .string()
    .trim()
    .min(1, "Describe the enterprise task to run.")
    .max(10_000, "Task text must not exceed 10,000 characters."),
  output_format: z.enum(["pdf", "json"]),
  require_approval: z.boolean(),
  max_steps: z.number().int().min(1).max(14).optional(),
});

type TaskForm = z.infer<typeof taskSchema>;

export function TaskCreatePage() {
  const navigate = useNavigate();
  const create = useCreateTask();
  const {
    register,
    watch,
    handleSubmit,
    formState: { errors },
  } = useForm<TaskForm>({
    resolver: zodResolver(taskSchema),
    defaultValues: {
      task_type: "supplier_quality_analysis.v1",
      task: "",
      output_format: "pdf",
      require_approval: false,
    },
  });
  const taskType = watch("task_type");

  const submit = handleSubmit(async (values) => {
    const created = await create.mutateAsync({
      task: values.task,
      task_type: values.task_type,
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
            <label htmlFor="task-type">Use case</label>
            <select id="task-type" {...register("task_type")}>
              <option value="supplier_quality_analysis.v1">
                Supplier Quality Analysis
              </option>
              <option value="accounts_payable_analysis.v1">
                Accounts Payable Invoice Analysis
              </option>
            </select>
            <p className="field-help">
              This requests one of the task types allowed by the authenticated
              server identity. It does not grant a role or data scope.
            </p>
          </div>
          <div className="field">
            <label htmlFor="task">What do you want the Agent to do?</label>
            <textarea
              id="task"
              rows={8}
              placeholder={
                taskType === "accounts_payable_analysis.v1"
                  ? "Analyze Accounts Payable invoice exceptions from 2026-04-01 to 2026-06-30 and generate a PDF report."
                  : "Analyze supplier quality for Q2 2026, compare with Q1, check the approved quality policy, and generate a PDF report."
              }
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
                {taskType === "accounts_payable_analysis.v1"
                  ? "Include an explicit inclusive date range. Legal entity and data authority come from the trusted server context."
                  : "Include an explicit year and quarter. Identity and data scope come from the trusted server context."}
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
                max={14}
                aria-invalid={Boolean(errors.max_steps)}
                {...register("max_steps", {
                  setValueAs: (value: string) =>
                    value === "" ? undefined : Number(value),
                })}
              />
              {errors.max_steps && (
                <p className="field-error">Use a value from 1 to 14.</p>
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
              {create.isPending ? "Submitting task…" : "Submit task"}
            </button>
          </div>
        </form>
      </section>
      <aside className="boundary-callout">
        <strong>Governance boundary</strong>
        <p>
          The browser cannot choose a tenant, role, supplier, legal-entity or
          business-unit permission, database, RAG source, rule set, or tool.
          Those constraints remain server-owned.
        </p>
      </aside>
    </>
  );
}
