/**
 * Marketing route group layout: grounds the journey on the deepest brand
 * background so overscroll and load-in never flash a lighter surface.
 */
export default function MarketingLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <div className="min-h-screen bg-void">{children}</div>;
}
