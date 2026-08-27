/** Feed-health square: ink OK, oxblood failing, hollow dormant/pending. Failures always carry the reason in label. */
export interface HealthDotProps {
  state: 'ok' | 'fail' | 'idle';
  /** e.g. "FAILING - feed truncated to 3 items since W33" */ label?: string;
}
export declare function HealthDot(props: HealthDotProps): JSX.Element;
