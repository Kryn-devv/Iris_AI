import { Skeleton } from "@/components/ui/misc";

export default function DashboardLoading() {
  return (
    <div>
      <Skeleton className="mb-6 h-8 w-48" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Skeleton className="h-28" />
          <Skeleton className="h-64" />
        </div>
        <div className="space-y-4">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    </div>
  );
}
