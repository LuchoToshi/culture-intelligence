/** Source tier badge: solid ink = core, outlined = watch, gray outline = candidate/dormant. */
export interface TierBadgeProps {
  tier: 'core' | 'watch' | 'candidate' | 'dormant';
}
export declare function TierBadge(props: TierBadgeProps): JSX.Element;
