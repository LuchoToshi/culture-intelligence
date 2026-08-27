/** Registry index row: serif name + divergence dagger, stage, counts, three independent meters.
 * @startingPoint section="Data" subtitle="Broadsheet signal index row" viewport="700x120"
 */
export interface SignalRowProps {
  signal: { name: string; stage: string; evidence: number; cities: string[]; edi: number; ado: number; sat: number; span: string };
  href?: string;
  onPin?: (s: any) => void;
  pinned?: boolean;
  /** 12px vs 20px vertical padding */ dense?: boolean;
}
export declare function SignalRow(props: SignalRowProps): JSX.Element;
