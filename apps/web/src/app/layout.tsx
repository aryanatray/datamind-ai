import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "DataMind.AI — Your data, one conversation", description: "A private workspace for grounded data analysis." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><body>{children}</body></html>;
}
