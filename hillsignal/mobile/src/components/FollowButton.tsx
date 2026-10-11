import { Alert } from 'react-native';
import { useFollow } from '@/api/hooks';
import type { WatchKind } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { openSignIn } from '@/lib/navigation';
import { Button } from './Button';

export function FollowButton({ kind, targetId }: { kind: WatchKind; targetId: string }) {
  const { isAuthed } = useAuth();
  const { isFollowing, toggle, isPending } = useFollow(kind, targetId);

  const onPress = () => {
    if (!isAuthed) {
      Alert.alert('Sign in to follow', 'Create a free account to build a watchlist and get a personal feed.', [
        { text: 'Not now', style: 'cancel' },
        { text: 'Sign in', onPress: openSignIn },
      ]);
      return;
    }
    // 402 (watchlist limit reached) is handled globally and opens the paywall.
    toggle();
  };

  return (
    <Button
      title={isFollowing ? 'Following' : 'Follow'}
      icon={isFollowing ? 'checkmark' : 'add'}
      variant={isFollowing ? 'secondary' : 'primary'}
      small
      loading={isPending}
      onPress={onPress}
    />
  );
}
