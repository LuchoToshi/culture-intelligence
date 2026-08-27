/** Uppercase letterspaced section header over a hairline (or 2px) rule. */
export interface SectionHeaderProps {
  title: string;
  /** gray uppercase annotation after title */ meta?: string;
  /** right-aligned slot */ right?: React.ReactNode;
  /** 'heavy' = 2px rule for part-level sections */ weight?: 'light' | 'heavy';
}
export declare function SectionHeader(props: SectionHeaderProps): JSX.Element;
