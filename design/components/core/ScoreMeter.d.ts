/** Typographic 1-5 meter for editorial / adoption / saturation scores.
 * @startingPoint section="Data" subtitle="Five-cell typographic score meter" viewport="700x150"
 */
export interface ScoreMeterProps {
  /** filled cells */ value: number;
  /** default 5 */ max?: number;
  /** oxblood fill when value >= 4 (saturation semantics) */ accent?: boolean;
  /** show n/5 numeral */ showNumeral?: boolean;
  /** cell width px (12 index, 14 compare, 22 dossier) */ cellWidth?: number;
}
export declare function ScoreMeter(props: ScoreMeterProps): JSX.Element;
