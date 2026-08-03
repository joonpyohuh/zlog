import {NextResponse} from 'next/server';
import {createClient} from '@/lib/supabase/server';
import {getUserSubscription} from '@/lib/billing';
import {getPaddleServer} from '@/lib/paddle/server';

export async function POST() {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({error: 'Unauthorized'}, {status: 401});
  }

  const subscription = await getUserSubscription(user.id);
  const customerId = subscription?.paddle_customer_id;
  if (!customerId) {
    return NextResponse.json({error: 'No Paddle customer on file'}, {status: 404});
  }

  try {
    const paddle = getPaddleServer();
    const subscriptionIds = subscription.paddle_subscription_id
      ? [subscription.paddle_subscription_id]
      : [];
    const session = await paddle.customerPortalSessions.create(customerId, subscriptionIds);
    const url = session.urls.general.overview;
    if (!url) {
      return NextResponse.json({error: 'Portal URL unavailable'}, {status: 500});
    }
    return NextResponse.json({url});
  } catch {
    console.error('customer portal session failed');
    return NextResponse.json({error: 'Failed to create portal session'}, {status: 500});
  }
}
