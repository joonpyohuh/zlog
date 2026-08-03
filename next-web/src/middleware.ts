import {createServerClient} from '@supabase/ssr';
import {NextResponse, type NextRequest} from 'next/server';

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({request});

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({name, value}) => request.cookies.set(name, value));
          response = NextResponse.next({request});
          cookiesToSet.forEach(({name, value, options}) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const {
    data: {user},
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const needsAuth =
    path.startsWith('/settings') ||
    path.startsWith('/studio');

  if (needsAuth && !user) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('next', path);
    return NextResponse.redirect(url);
  }

  if (path === '/login' && user) {
    const url = request.nextUrl.clone();
    url.pathname = '/settings/billing';
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: ['/settings/:path*', '/studio/:path*', '/login'],
};
