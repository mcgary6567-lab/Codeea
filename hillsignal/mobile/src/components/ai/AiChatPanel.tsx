import { Ionicons } from '@expo/vector-icons';
import { useMutation } from '@tanstack/react-query';
import { router } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Pressable, TextInput, View } from 'react-native';
import { errorMessage, isApiError } from '@/api/client';
import { api } from '@/api/endpoints';
import type { ChatMessage } from '@/api/types';
import { useAuth } from '@/auth/AuthContext';
import { config } from '@/config';
import { openPaywall } from '@/lib/navigation';
import { radius, spacing, useTheme } from '@/theme';
import { Card } from '../Card';
import { Disclaimer } from '../Disclaimer';
import { LockedCard } from '../LockedCard';
import { Notice } from '../Notice';
import { Txt } from '../Txt';

/** API rule: ≤ 20 messages and the last one must be from the user. */
export function trimForApi(messages: ChatMessage[]): ChatMessage[] {
  let out = messages.slice(-config.CHAT_MAX_MESSAGES);
  while (out.length && out[0]?.role === 'assistant' && out.length > 1) out = out.slice(1);
  return out;
}

type UiMessage = ChatMessage & { disclaimer?: string };

function Bubble({ m }: { m: UiMessage }) {
  const t = useTheme();
  const mine = m.role === 'user';
  return (
    <View style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: '88%' }}>
      <View
        style={{
          backgroundColor: mine ? t.accent : t.surfaceAlt,
          borderRadius: radius.lg,
          borderBottomRightRadius: mine ? 4 : radius.lg,
          borderBottomLeftRadius: mine ? radius.lg : 4,
          paddingHorizontal: spacing.md,
          paddingVertical: spacing.sm + 2,
        }}
      >
        <Txt style={{ color: mine ? t.onAccent : t.text, lineHeight: 21 }} selectable>
          {m.content}
        </Txt>
      </View>
      {mine ? null : <Disclaimer text={m.disclaimer} />}
    </View>
  );
}

export function AiChatPanel() {
  const t = useTheme();
  const { isAuthed, entitlements } = useAuth();
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [text, setText] = useState('');
  const chat = useMutation({
    mutationFn: (history: UiMessage[]) => api.aiChat(trimForApi(history.map(({ role, content }) => ({ role, content })))),
    onSuccess: (r) => setMessages((m) => [...m, { role: 'assistant', content: r.reply, disclaimer: r.disclaimer }]),
  });

  if (!entitlements.aiChat) {
    return (
      <LockedCard
        title="Ask HillSignal"
        message="Chat with an analyst that has read every filing: follow-up questions, comparisons, context."
        requiredPlan="premium"
      />
    );
  }
  if (!isAuthed) return <Notice message="Sign in to chat." actionLabel="Sign in" onAction={() => router.push('/auth/sign-in')} />;

  const send = () => {
    const content = text.trim();
    if (!content || chat.isPending) return;
    const next: UiMessage[] = [...messages, { role: 'user', content }];
    setMessages(next);
    setText('');
    chat.mutate(next);
  };

  return (
    <Card style={{ gap: spacing.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Txt variant="heading">Ask HillSignal</Txt>
        {messages.length ? (
          <Pressable onPress={() => setMessages([])} hitSlop={8} accessibilityLabel="New chat">
            <Ionicons name="refresh" size={18} color={t.textMuted} />
          </Pressable>
        ) : null}
      </View>
      {messages.length === 0 ? (
        <Txt tone="muted" variant="small">
          Ask follow-ups like "How does that compare to last year?" Conversations aren't saved.
        </Txt>
      ) : null}
      {messages.map((m, i) => (
        <Bubble key={i} m={m} />
      ))}
      {chat.isPending ? <ActivityIndicator color={t.accent} style={{ alignSelf: 'flex-start' }} /> : null}
      {chat.error ? (
        <Notice
          kind="error"
          message={errorMessage(chat.error)}
          actionLabel={
            isApiError(chat.error) && chat.error.status === 402
              ? 'Upgrade'
              : isApiError(chat.error) && chat.error.code === 'ai_declined'
                ? undefined
                : 'Retry'
          }
          onAction={() => (isApiError(chat.error) && chat.error.status === 402 ? openPaywall('premium') : chat.mutate(messages))}
        />
      ) : null}
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm }}>
        <TextInput
          value={text}
          onChangeText={setText}
          placeholder="Message"
          placeholderTextColor={t.textFaint}
          multiline
          maxLength={2000}
          style={{
            flex: 1,
            color: t.text,
            backgroundColor: t.surfaceAlt,
            borderRadius: radius.md,
            borderWidth: 1,
            borderColor: t.border,
            paddingHorizontal: spacing.md,
            paddingVertical: spacing.sm + 2,
            maxHeight: 120,
            fontSize: 15,
          }}
          accessibilityLabel="Chat message"
        />
        <Pressable
          onPress={send}
          disabled={!text.trim() || chat.isPending}
          accessibilityRole="button"
          accessibilityLabel="Send"
          style={{ backgroundColor: t.accent, borderRadius: radius.md, padding: spacing.md - 1, opacity: !text.trim() || chat.isPending ? 0.5 : 1 }}
        >
          <Ionicons name="send" size={17} color={t.onAccent} />
        </Pressable>
      </View>
    </Card>
  );
}
