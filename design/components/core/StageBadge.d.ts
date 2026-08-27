/** Lifecycle stage marker: oxblood dot = emerging, ink = strengthening, gray = mainstream, hollow = saturated. */
export interface StageBadgeProps {
  stage: 'emerging' | 'strengthening' | 'mainstream' | 'saturated';
}
export declare function StageBadge(props: StageBadgeProps): JSX.Element;
