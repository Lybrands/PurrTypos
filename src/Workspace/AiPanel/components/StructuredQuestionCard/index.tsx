import React from "react";
import type { StructuredQuestion } from "../../structuredQuestions";

interface StructuredQuestionCardProps {
  questions: StructuredQuestion[];
  disabled?: boolean;
  onAnswer?: (answer: string) => void;
}

export default function StructuredQuestionCard({
  questions,
  disabled = false,
  onAnswer,
}: StructuredQuestionCardProps) {
  const [selections, setSelections] = React.useState<Record<number, string>>({});
  const multiple = questions.length > 1;
  const allSelected =
    multiple && questions.every((_question, index) => selections[index]);
  const questionKey = questions
    .map((item) => `${item.question}\u0000${item.options.join("\u0001")}`)
    .join("\u0002");

  React.useEffect(() => {
    setSelections({});
  }, [questionKey]);

  const selectOption = (questionIndex: number, option: string) => {
    if (!multiple) {
      onAnswer?.(option);
      return;
    }
    setSelections((current) => ({ ...current, [questionIndex]: option }));
  };

  const submitMultipleAnswers = () => {
    if (!allSelected) return;
    const answer = questions
      .map(
        (item, index) =>
          `${index + 1}. ${item.question}\n回答：${selections[index]}`,
      )
      .join("\n\n");
    onAnswer?.(answer);
  };

  return (
    <div className="structured-question-list" aria-label="AI 提问">
      {questions.map((item, questionIndex) => (
        <section
          className="structured-question-card"
          key={`${questionIndex}-${item.question}`}
        >
          <div className="structured-question-card__eyebrow">
            {questions.length > 1 ? `问题 ${questionIndex + 1}` : "请选择一项"}
          </div>
          <p className="structured-question-card__question">{item.question}</p>
          <div className="structured-question-card__options">
            {item.options.map((option) => (
              <button
                type="button"
                key={option}
                className={selections[questionIndex] === option ? "is-selected" : ""}
                disabled={disabled || !onAnswer}
                aria-pressed={multiple ? selections[questionIndex] === option : undefined}
                onClick={() => selectOption(questionIndex, option)}
              >
                {option}
              </button>
            ))}
          </div>
        </section>
      ))}
      {multiple && (
        <button
          type="button"
          className="structured-question-list__submit"
          disabled={disabled || !onAnswer || !allSelected}
          onClick={submitMultipleAnswers}
        >
          提交选择
        </button>
      )}
    </div>
  );
}
