/** Broadsheet masthead: brand wordmark left, uppercase nav right, 2px rule below.
 * @startingPoint section="Navigation" subtitle="Wordmark + uppercase nav over 2px rule" viewport="700x90"
 */
export interface MastheadProps {
  items: { label: string; href: string }[];
  /** label of the current screen (rendered underlined, not a link) */ active?: string;
  /** shows the Search ⌘K affordance */ onSearch?: () => void;
}
export declare function Masthead(props: MastheadProps): JSX.Element;
