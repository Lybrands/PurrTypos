import React from 'react'
import '../styles/purr.scss'

export interface PurrTypographyLinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  ellipsis?: boolean
}

const PurrTypographyLink = React.forwardRef<HTMLAnchorElement, PurrTypographyLinkProps>(
  function PurrTypographyLink({ ellipsis, ...props }, ref) {
    return (
      <a
        {...props}
        ref={ref}
        className={[
          'purr-typography-link',
          ellipsis && 'purr-typography-link--ellipsis',
          props.className,
        ].filter(Boolean).join(' ')}
      />
    )
  },
)

export const PurrTypography = {
  Link: PurrTypographyLink,
}
