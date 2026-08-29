import type { Metadata } from "next";
import { brand } from "@/config/brand";
import { Experience } from "@/components/marketing/Experience";

export const metadata: Metadata = {
  title: `${brand.name} — ${brand.tagline}`,
  description: brand.description,
};

/**
 * The marketing landing page: a scroll-driven cinematic journey through a
 * living 3D feedback universe. The server renders the full static experience
 * (real copy, CTAs — crawlable); capable clients upgrade to the 3D journey.
 */
export default function MarketingPage() {
  return <Experience />;
}
