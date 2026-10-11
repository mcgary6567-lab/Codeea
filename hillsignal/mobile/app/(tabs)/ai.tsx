import { KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import { AiChatPanel } from '@/components/ai/AiChatPanel';
import { AiSearchPanel } from '@/components/ai/AiSearchPanel';
import { SectionHeader } from '@/components/SectionHeader';
import { spacing, useTheme } from '@/theme';

export default function AiScreen() {
  const t = useTheme();
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: t.bg }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
        <SectionHeader title="Search in plain English" />
        <AiSearchPanel />
        <SectionHeader title="Chat · Premium" />
        <AiChatPanel />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
