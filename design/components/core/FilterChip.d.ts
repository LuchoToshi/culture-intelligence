/** Square filter chip: solid ink when active, borderless text otherwise. */
export interface FilterChipProps {
  label: string;
  active?: boolean;
  onClick?: () => void;
}
export declare function FilterChip(props: FilterChipProps): JSX.Element;
