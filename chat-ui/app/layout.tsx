import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trợ lý Tài liệu Nội bộ",
  description: "Giao diện chat RAG nội bộ",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi">
      <body className="bg-[#0f0f12] text-gray-100 antialiased">{children}</body>
    </html>
  );
}
