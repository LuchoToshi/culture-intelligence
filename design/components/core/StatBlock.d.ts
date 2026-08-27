/** Header stat: serif numeral over uppercase label; accent renders the numeral oxblood. */
export interface StatBlockProps {
  value: string | number;
  label: string;
  accent?: boolean;
}
export declare function StatBlock(props: StatBlockProps): JSX.Element;
