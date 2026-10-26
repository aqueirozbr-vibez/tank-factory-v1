import { Shell } from "@/components/Shell";
import { Placeholder } from "@/components/Placeholder";

export const dynamic = "force-dynamic";

export default async function TasksPage() {
  return (
    <Shell title="Tasks" current="/tasks">
      <Placeholder
        screen="Tasks"
        sections={[
          "TaskStrip per task",
          "status, risk, PR links",
          "opens /tasks/[taskId]",
        ]}
      />
    </Shell>
  );
}
