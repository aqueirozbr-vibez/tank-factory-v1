import { Shell } from "@/components/Shell";
import { Placeholder } from "@/components/Placeholder";

export const dynamic = "force-dynamic";

export default async function ReviewIndexPage() {
  return (
    <Shell title="Review findings" current="/tasks/review">
      <Placeholder
        screen="Review findings"
        sections={[
          "Pick a task to see its review rounds",
        ]}
      />
    </Shell>
  );
}
