import { useEffect } from 'react';
import { RichTextEditor as MantineRTE, Link } from '@mantine/tiptap';
import { useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Highlight from '@tiptap/extension-highlight';
import TextAlign from '@tiptap/extension-text-align';
import '@mantine/tiptap/styles.css';

interface RichTextEditorProps {
  content: string;
  onChange: (html: string) => void;
  minHeight?: string;
}

export default function RichTextEditor({ content, onChange, minHeight = '300px' }: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      Link,
      Highlight,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
    ],
    content,
    onUpdate: ({ editor }) => {
      onChange(editor.getHTML());
    },
  });

  // Update editor content when external content changes (e.g., LLM generation)
  useEffect(() => {
    if (editor && content !== editor.getHTML()) {
      editor.commands.setContent(content);
    }
  }, [content, editor]);

  return (
    <MantineRTE
      editor={editor}
      styles={{
        root: {
          border: '1px solid #1a1f2e',
          borderRadius: 6,
          background: '#050608',
        },
        toolbar: {
          background: '#0e1117',
          borderBottom: '1px solid #1a1f2e',
        },
        content: {
          background: '#050608',
          color: '#e8edf2',
          minHeight,
          fontFamily: "'Rajdhani', sans-serif",
          fontSize: '14px',
          '& .ProseMirror': {
            padding: '12px',
          },
        },
        control: {
          color: '#5a6478',
          border: 'none',
          '&:hover': {
            background: 'rgba(0, 212, 255, 0.1)',
          },
          '&[data-active]': {
            background: 'rgba(0, 212, 255, 0.2)',
            color: '#00d4ff',
          },
        },
      }}
    >
      <MantineRTE.Toolbar sticky stickyOffset={60}>
        <MantineRTE.ControlsGroup>
          <MantineRTE.Bold />
          <MantineRTE.Italic />
          <MantineRTE.Underline />
          <MantineRTE.Strikethrough />
          <MantineRTE.Highlight />
        </MantineRTE.ControlsGroup>

        <MantineRTE.ControlsGroup>
          <MantineRTE.H1 />
          <MantineRTE.H2 />
          <MantineRTE.H3 />
        </MantineRTE.ControlsGroup>

        <MantineRTE.ControlsGroup>
          <MantineRTE.BulletList />
          <MantineRTE.OrderedList />
        </MantineRTE.ControlsGroup>

        <MantineRTE.ControlsGroup>
          <MantineRTE.AlignLeft />
          <MantineRTE.AlignCenter />
          <MantineRTE.AlignRight />
        </MantineRTE.ControlsGroup>

        <MantineRTE.ControlsGroup>
          <MantineRTE.Blockquote />
          <MantineRTE.Hr />
        </MantineRTE.ControlsGroup>

        <MantineRTE.ControlsGroup>
          <MantineRTE.Undo />
          <MantineRTE.Redo />
        </MantineRTE.ControlsGroup>
      </MantineRTE.Toolbar>

      <MantineRTE.Content />
    </MantineRTE>
  );
}
