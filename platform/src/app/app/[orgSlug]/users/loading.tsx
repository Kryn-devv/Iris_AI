import { Skeleton } from "@/components/ui/misc";

export default function UsersLoading() {
  return (
    <div>
      <Skeleton className="mb-2 h-6 w-32" />
      <Skeleton className="mb-6 h-4 w-72" />
      <Skeleton className="mb-4 h-9 w-80" />
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    </div>
  );
}
