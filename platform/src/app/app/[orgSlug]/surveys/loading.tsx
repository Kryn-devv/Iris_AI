import { Skeleton } from "@/components/ui/misc";

export default function SurveysLoading() {
  return (
    <div>
      <div className="mb-6">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="mt-2 h-4 w-72" />
      </div>
      <div className="space-y-px overflow-hidden rounded-xl border border-line">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-surface-raised px-5 py-4">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="mt-2 h-3 w-1/2" />
          </div>
        ))}
      </div>
    </div>
  );
}
