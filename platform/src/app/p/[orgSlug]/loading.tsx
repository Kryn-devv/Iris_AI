import { Skeleton } from "@/components/ui/misc";

export default function PortalBoardLoading() {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-11 w-40" />
      </div>
      <div className="space-y-2.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}
