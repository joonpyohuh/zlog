import type {Metadata} from 'next';
import {IBM_Plex_Sans, Archivo_Black} from 'next/font/google';
import {PaddleProvider} from '@/components/paddle/PaddleProvider';
import {SiteHeader} from '@/components/SiteHeader';
import './globals.css';

const plex = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-plex',
});

const archivo = Archivo_Black({
  subsets: ['latin'],
  weight: '400',
  variable: '--font-brand',
});

export const metadata: Metadata = {
  title: 'zlog',
  description: 'Photo. Video. Words. Film.',
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return (
    <html lang="en">
      <body className={`${plex.variable} ${archivo.variable} antialiased`}>
        <PaddleProvider>
          <SiteHeader />
          {children}
        </PaddleProvider>
      </body>
    </html>
  );
}
